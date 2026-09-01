use std::fs;
use std::net::{Ipv4Addr, SocketAddrV4};
use std::path::PathBuf;
use std::process::ExitCode;
use std::str::FromStr;
use std::time::Duration;

use clap::{Parser, Subcommand};
use serde::Deserialize;
use serde::Serialize;
use serde_json::{Value, json};
use ue_remote_client::client::{ClientConfig, ClientError, ExpectedIdentity, RemoteClient};

#[derive(Debug, Parser)]
#[command(name = "ue-remote-client", version)]
#[command(about = "Independent localhost client for Unreal Python Remote Execution")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Discover local Unreal Remote Execution nodes without running code.
    Discover(DiscoverArgs),
    /// Verify one exact Editor by PID/project without running a business script.
    Verify(VerifyArgs),
    /// Verify one exact Editor by PID/project and execute a UTF-8 Python file.
    Execute(ExecuteArgs),
    /// Verify one exact Editor and execute ordered UTF-8 Python files on one connection.
    ExecutePlan(ExecutePlanArgs),
}

#[derive(Clone, Debug, clap::Args)]
struct DiscoverArgs {
    #[command(flatten)]
    common: CommonArgs,
    /// Include Unreal-provided metadata. It may contain local paths and user names.
    #[arg(long, default_value_t = false)]
    include_metadata: bool,
}

#[derive(Clone, Debug, clap::Args)]
struct CommonArgs {
    #[arg(long, default_value = "239.0.0.1:6766")]
    multicast_endpoint: String,
    #[arg(long, default_value = "127.0.0.1")]
    multicast_bind_address: String,
    #[arg(long, default_value_t = 3.0, value_parser = positive_seconds)]
    discovery_timeout_secs: f64,
    #[arg(long, default_value_t = 0.35, value_parser = positive_seconds)]
    discovery_settle_secs: f64,
}

#[derive(Clone, Debug, clap::Args)]
struct ExecuteArgs {
    #[command(flatten)]
    common: CommonArgs,
    #[arg(long)]
    script: PathBuf,
    #[arg(long)]
    expected_pid: u32,
    #[arg(long)]
    expected_project_dir: String,
    #[arg(long)]
    node_id_hint: Option<String>,
    #[arg(long, default_value_t = 8.0, value_parser = positive_seconds)]
    connect_timeout_secs: f64,
    #[arg(long, default_value_t = 3600.0, value_parser = positive_seconds)]
    command_timeout_secs: f64,
    #[arg(long, default_value_t = 16 * 1024 * 1024)]
    max_response_bytes: usize,
}

#[derive(Clone, Debug, clap::Args)]
struct VerifyArgs {
    #[command(flatten)]
    common: CommonArgs,
    #[arg(long)]
    expected_pid: u32,
    #[arg(long)]
    expected_project_dir: String,
    #[arg(long)]
    node_id_hint: Option<String>,
    #[arg(long, default_value_t = 8.0, value_parser = positive_seconds)]
    connect_timeout_secs: f64,
    #[arg(long, default_value_t = 16 * 1024 * 1024)]
    max_response_bytes: usize,
}

#[derive(Clone, Debug, clap::Args)]
struct ExecutePlanArgs {
    #[command(flatten)]
    common: CommonArgs,
    #[arg(long)]
    plan: PathBuf,
    #[arg(long)]
    expected_pid: u32,
    #[arg(long)]
    expected_project_dir: String,
    #[arg(long)]
    node_id_hint: Option<String>,
    #[arg(long, default_value_t = 8.0, value_parser = positive_seconds)]
    connect_timeout_secs: f64,
    #[arg(long, default_value_t = 3600.0, value_parser = positive_seconds)]
    command_timeout_secs: f64,
    #[arg(long, default_value_t = 16 * 1024 * 1024)]
    max_response_bytes: usize,
}

#[derive(Debug, Deserialize)]
struct ExecutionPlan {
    scripts: Vec<PathBuf>,
}

#[derive(Serialize)]
struct CliResult {
    result_version: u32,
    status: &'static str,
    action: &'static str,
    message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    data: Option<Value>,
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    let (result, exit_code) = match cli.command {
        Command::Discover(args) => run_discover(args),
        Command::Verify(args) => run_verify(args),
        Command::Execute(args) => run_execute(args),
        Command::ExecutePlan(args) => run_execute_plan(args),
    };
    println!(
        "{}",
        serde_json::to_string(&result).unwrap_or_else(|error| {
            format!(
                "{{\"result_version\":1,\"status\":\"failed\",\"action\":\"serialize\",\"message\":{}}}",
                serde_json::to_string(&error.to_string()).unwrap()
            )
        })
    );
    ExitCode::from(exit_code)
}

fn run_discover(args: DiscoverArgs) -> (CliResult, u8) {
    let config = match common_config(&args.common) {
        Ok(value) => value,
        Err(error) => return known_failure("discover", error),
    };
    match RemoteClient::new(config).and_then(|client| client.discover()) {
        Ok(nodes) => {
            let node_data = if args.include_metadata {
                serde_json::to_value(&nodes).unwrap()
            } else {
                json!(
                    nodes
                        .iter()
                        .map(|node| json!({"node_id": node.id}))
                        .collect::<Vec<_>>()
                )
            };
            (
                CliResult {
                    result_version: 1,
                    status: "succeeded",
                    action: "discover",
                    message: format!("discovered {} node(s)", nodes.len()),
                    data: Some(json!({"nodes": node_data})),
                },
                0,
            )
        }
        Err(error) => classify_error("discover", error),
    }
}

fn run_verify(args: VerifyArgs) -> (CliResult, u8) {
    let mut config = match common_config(&args.common) {
        Ok(value) => value,
        Err(error) => return known_failure("verify", error),
    };
    config.connect_timeout = Duration::from_secs_f64(args.connect_timeout_secs);
    config.max_response_bytes = args.max_response_bytes;
    let expected = ExpectedIdentity {
        pid: args.expected_pid,
        project_dir: args.expected_project_dir,
    };
    match RemoteClient::new(config)
        .and_then(|client| client.verify_identity(&expected, args.node_id_hint.as_deref()))
    {
        Ok(identity) => (
            CliResult {
                result_version: 1,
                status: "succeeded",
                action: "verify",
                message: "exact editor identity verified".to_owned(),
                data: Some(serde_json::to_value(identity).unwrap()),
            },
            0,
        ),
        Err(error) => classify_error("verify", error),
    }
}

fn run_execute(args: ExecuteArgs) -> (CliResult, u8) {
    let source = match fs::read_to_string(&args.script) {
        Ok(value) => value,
        Err(error) => return known_failure("execute", ClientError::Io(error)),
    };
    let mut config = match common_config(&args.common) {
        Ok(value) => value,
        Err(error) => return known_failure("execute", error),
    };
    config.connect_timeout = Duration::from_secs_f64(args.connect_timeout_secs);
    config.command_timeout = Duration::from_secs_f64(args.command_timeout_secs);
    config.max_response_bytes = args.max_response_bytes;
    let expected = ExpectedIdentity {
        pid: args.expected_pid,
        project_dir: args.expected_project_dir,
    };
    match RemoteClient::new(config).and_then(|client| {
        client.execute_verified(&expected, &source, args.node_id_hint.as_deref())
    }) {
        Ok(execution) => {
            let success = execution.result.success;
            (
                CliResult {
                    result_version: 1,
                    status: if success { "succeeded" } else { "failed" },
                    action: "execute",
                    message: if success {
                        "remote command completed".to_owned()
                    } else {
                        "remote command reported failure".to_owned()
                    },
                    data: Some(serde_json::to_value(execution).unwrap()),
                },
                if success { 0 } else { 1 },
            )
        }
        Err(error) => classify_error("execute", error),
    }
}

fn run_execute_plan(args: ExecutePlanArgs) -> (CliResult, u8) {
    let plan_text = match fs::read_to_string(&args.plan) {
        Ok(value) => value,
        Err(error) => return known_failure("execute-plan", ClientError::Io(error)),
    };
    let plan: ExecutionPlan = match serde_json::from_str(&plan_text) {
        Ok(value) => value,
        Err(error) => return known_failure("execute-plan", ClientError::Json(error)),
    };
    if plan.scripts.is_empty() {
        return known_failure(
            "execute-plan",
            ClientError::Configuration("execution plan must contain scripts".to_owned()),
        );
    }
    let base = args
        .plan
        .parent()
        .unwrap_or_else(|| std::path::Path::new("."));
    let mut sources = Vec::with_capacity(plan.scripts.len());
    for script in plan.scripts {
        let path = if script.is_absolute() {
            script
        } else {
            base.join(script)
        };
        match fs::read_to_string(path) {
            Ok(value) => sources.push(value),
            Err(error) => return known_failure("execute-plan", ClientError::Io(error)),
        }
    }
    let mut config = match common_config(&args.common) {
        Ok(value) => value,
        Err(error) => return known_failure("execute-plan", error),
    };
    config.connect_timeout = Duration::from_secs_f64(args.connect_timeout_secs);
    config.command_timeout = Duration::from_secs_f64(args.command_timeout_secs);
    config.max_response_bytes = args.max_response_bytes;
    let expected = ExpectedIdentity {
        pid: args.expected_pid,
        project_dir: args.expected_project_dir,
    };
    match RemoteClient::new(config).and_then(|client| {
        client.execute_plan_verified(&expected, &sources, args.node_id_hint.as_deref())
    }) {
        Ok(execution) => {
            let success = execution.results.iter().all(|result| result.success);
            (
                CliResult {
                    result_version: 1,
                    status: if success { "succeeded" } else { "failed" },
                    action: "execute-plan",
                    message: if success {
                        "remote plan completed".to_owned()
                    } else {
                        "remote plan stopped after a reported failure".to_owned()
                    },
                    data: Some(serde_json::to_value(execution).unwrap()),
                },
                if success { 0 } else { 1 },
            )
        }
        Err(error) => classify_error("execute-plan", error),
    }
}

fn common_config(args: &CommonArgs) -> Result<ClientConfig, ClientError> {
    let multicast_endpoint = SocketAddrV4::from_str(&args.multicast_endpoint).map_err(|error| {
        ClientError::Configuration(format!("invalid multicast endpoint: {error}"))
    })?;
    let multicast_bind_address = Ipv4Addr::from_str(&args.multicast_bind_address)
        .map_err(|error| ClientError::Configuration(format!("invalid bind address: {error}")))?;
    Ok(ClientConfig {
        multicast_endpoint,
        multicast_bind_address,
        discovery_timeout: Duration::from_secs_f64(args.discovery_timeout_secs),
        discovery_settle: Duration::from_secs_f64(args.discovery_settle_secs),
        ..ClientConfig::default()
    })
}

fn classify_error(action: &'static str, error: ClientError) -> (CliResult, u8) {
    match error {
        ClientError::OutcomeUnknown(message) => (
            CliResult {
                result_version: 1,
                status: "outcome_unknown",
                action,
                message,
                data: None,
            },
            3,
        ),
        other => known_failure(action, other),
    }
}

fn known_failure(action: &'static str, error: ClientError) -> (CliResult, u8) {
    (
        CliResult {
            result_version: 1,
            status: "failed",
            action,
            message: error.to_string(),
            data: None,
        },
        1,
    )
}

fn positive_seconds(value: &str) -> Result<f64, String> {
    let parsed = value
        .parse::<f64>()
        .map_err(|error| format!("invalid number: {error}"))?;
    if parsed.is_finite() && parsed > 0.0 {
        Ok(parsed)
    } else {
        Err("value must be a positive finite number".to_owned())
    }
}

#[cfg(test)]
mod tests {
    use super::positive_seconds;

    #[test]
    fn seconds_parser_rejects_zero_nan_and_infinity() {
        assert!(positive_seconds("0").is_err());
        assert!(positive_seconds("NaN").is_err());
        assert!(positive_seconds("inf").is_err());
        assert_eq!(positive_seconds("0.25").unwrap(), 0.25);
    }
}
