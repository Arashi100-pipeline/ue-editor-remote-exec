use std::collections::BTreeMap;
use std::io::{ErrorKind, Read, Write};
use std::net::{Ipv4Addr, SocketAddr, SocketAddrV4, TcpListener, TcpStream, UdpSocket};
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use socket2::{Domain, Protocol, Socket, Type};
use uuid::Uuid;

use crate::protocol::{
    CommandResult, KIND_CLOSE, KIND_COMMAND, KIND_OPEN, KIND_PING, KIND_PONG, Message,
    ProtocolError, command_result,
};

const IDENTITY_MARKER: &str = "UE_REMOTE_RUST_IDENTITY=";

#[derive(Clone, Debug)]
pub struct ClientConfig {
    pub multicast_endpoint: SocketAddrV4,
    pub multicast_bind_address: Ipv4Addr,
    pub multicast_ttl: u32,
    pub callback_address: Ipv4Addr,
    pub discovery_timeout: Duration,
    pub discovery_settle: Duration,
    pub connect_timeout: Duration,
    pub command_timeout: Duration,
    pub max_response_bytes: usize,
}

impl Default for ClientConfig {
    fn default() -> Self {
        Self {
            multicast_endpoint: SocketAddrV4::new(Ipv4Addr::new(239, 0, 0, 1), 6766),
            multicast_bind_address: Ipv4Addr::LOCALHOST,
            multicast_ttl: 0,
            callback_address: Ipv4Addr::LOCALHOST,
            discovery_timeout: Duration::from_secs(3),
            discovery_settle: Duration::from_millis(350),
            connect_timeout: Duration::from_secs(8),
            command_timeout: Duration::from_secs(3600),
            max_response_bytes: 16 * 1024 * 1024,
        }
    }
}

impl ClientConfig {
    pub fn validate(&self) -> Result<(), ClientError> {
        if !self.multicast_endpoint.ip().is_multicast() {
            return Err(ClientError::Configuration(
                "multicast endpoint must use an IPv4 multicast address".to_owned(),
            ));
        }
        if !self.multicast_bind_address.is_loopback() {
            return Err(ClientError::Configuration(
                "multicast bind address must be loopback".to_owned(),
            ));
        }
        if !self.callback_address.is_loopback() {
            return Err(ClientError::Configuration(
                "TCP callback address must be loopback".to_owned(),
            ));
        }
        if self.multicast_ttl != 0 {
            return Err(ClientError::Configuration(
                "multicast TTL must be zero for the public client".to_owned(),
            ));
        }
        if self.discovery_timeout.is_zero()
            || self.connect_timeout.is_zero()
            || self.command_timeout.is_zero()
        {
            return Err(ClientError::Configuration(
                "timeouts must be positive".to_owned(),
            ));
        }
        if self.max_response_bytes < 1024 {
            return Err(ClientError::Configuration(
                "max response size must be at least 1024 bytes".to_owned(),
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct Node {
    pub id: String,
    pub metadata: Option<Value>,
}

#[derive(Clone, Debug)]
pub struct ExpectedIdentity {
    pub pid: u32,
    pub project_dir: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct VerifiedExecution {
    pub node_id: String,
    pub result: CommandResult,
}

#[derive(Clone, Debug, Serialize)]
pub struct VerifiedIdentity {
    pub node_id: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct VerifiedPlanExecution {
    pub node_id: String,
    pub results: Vec<CommandResult>,
}

#[derive(Debug, thiserror::Error)]
pub enum ClientError {
    #[error("invalid client configuration: {0}")]
    Configuration(String),
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("protocol error: {0}")]
    Protocol(#[from] ProtocolError),
    #[error("no Unreal Remote Execution nodes were discovered")]
    DiscoveryTimeout,
    #[error("the selected node did not open its TCP callback before the deadline")]
    ConnectionTimeout,
    #[error("no discovered node matched the expected project and PID")]
    NoVerifiedNode,
    #[error("the command may have executed but no trustworthy final result arrived: {0}")]
    OutcomeUnknown(String),
}

pub struct RemoteClient {
    config: ClientConfig,
    client_id: String,
    discovery_socket: UdpSocket,
}

impl RemoteClient {
    pub fn new(config: ClientConfig) -> Result<Self, ClientError> {
        config.validate()?;
        let socket = create_discovery_socket(&config)?;
        Ok(Self {
            config,
            client_id: Uuid::new_v4().to_string(),
            discovery_socket: socket,
        })
    }

    pub fn discover(&self) -> Result<Vec<Node>, ClientError> {
        let deadline = Instant::now() + self.config.discovery_timeout;
        let mut next_ping = Instant::now();
        let mut first_seen = None;
        let mut nodes = BTreeMap::<String, Node>::new();
        let mut buffer = vec![0_u8; 64 * 1024];

        loop {
            let now = Instant::now();
            if now >= deadline
                || first_seen.is_some_and(|seen: Instant| {
                    now.duration_since(seen) >= self.config.discovery_settle
                })
            {
                break;
            }
            if now >= next_ping {
                self.send_discovery(&Message::new(KIND_PING, &self.client_id))?;
                next_ping = now + Duration::from_millis(250);
            }

            match self.discovery_socket.recv_from(&mut buffer) {
                Ok((size, _peer)) => {
                    let message: Message = match serde_json::from_slice(&buffer[..size]) {
                        Ok(value) => value,
                        Err(_) => continue,
                    };
                    if message.validate_for(&self.client_id).is_err() || message.kind != KIND_PONG {
                        continue;
                    }
                    first_seen.get_or_insert_with(Instant::now);
                    let node = Node {
                        id: message.source.clone(),
                        metadata: message.data,
                    };
                    nodes.insert(message.source, node);
                }
                Err(error) if is_timeout(&error) => {}
                Err(error) => return Err(error.into()),
            }
        }

        if nodes.is_empty() {
            Err(ClientError::DiscoveryTimeout)
        } else {
            Ok(nodes.into_values().collect())
        }
    }

    pub fn execute_verified(
        &self,
        expected: &ExpectedIdentity,
        source: &str,
        node_id_hint: Option<&str>,
    ) -> Result<VerifiedExecution, ClientError> {
        let (node, mut stream) = self.open_verified(expected, node_id_hint)?;
        let result = send_command(
            &mut stream,
            &self.client_id,
            &node.id,
            source,
            self.config.command_timeout,
            self.config.max_response_bytes,
        );
        self.close_connection(&node.id);
        result.map(|result| VerifiedExecution {
            node_id: node.id,
            result,
        })
    }

    pub fn verify_identity(
        &self,
        expected: &ExpectedIdentity,
        node_id_hint: Option<&str>,
    ) -> Result<VerifiedIdentity, ClientError> {
        let (node, _stream) = self.open_verified(expected, node_id_hint)?;
        self.close_connection(&node.id);
        Ok(VerifiedIdentity { node_id: node.id })
    }

    pub fn execute_plan_verified(
        &self,
        expected: &ExpectedIdentity,
        sources: &[String],
        node_id_hint: Option<&str>,
    ) -> Result<VerifiedPlanExecution, ClientError> {
        if sources.is_empty() {
            return Err(ClientError::Configuration(
                "execution plan must contain at least one script".to_owned(),
            ));
        }
        let (node, mut stream) = self.open_verified(expected, node_id_hint)?;
        let mut results = Vec::with_capacity(sources.len());
        for (index, source) in sources.iter().enumerate() {
            let result = send_command(
                &mut stream,
                &self.client_id,
                &node.id,
                source,
                self.config.command_timeout,
                self.config.max_response_bytes,
            )
            .map_err(|error| match error {
                ClientError::OutcomeUnknown(message) => ClientError::OutcomeUnknown(format!(
                    "plan step {index} may have executed: {message}"
                )),
                other => other,
            });
            match result {
                Ok(result) => {
                    let success = result.success;
                    results.push(result);
                    if !success {
                        break;
                    }
                }
                Err(error) => {
                    self.close_connection(&node.id);
                    return Err(error);
                }
            }
        }
        self.close_connection(&node.id);
        Ok(VerifiedPlanExecution {
            node_id: node.id,
            results,
        })
    }

    fn open_verified(
        &self,
        expected: &ExpectedIdentity,
        node_id_hint: Option<&str>,
    ) -> Result<(Node, TcpStream), ClientError> {
        let mut nodes = self.discover()?;
        nodes.sort_by_key(|node| usize::from(Some(node.id.as_str()) != node_id_hint));

        for node in nodes {
            let mut stream = match self.open_connection(&node.id) {
                Ok(value) => value,
                Err(ClientError::ConnectionTimeout) => continue,
                Err(error) => return Err(error),
            };
            let probe = identity_probe_source();
            let probe_result = match send_command(
                &mut stream,
                &self.client_id,
                &node.id,
                &probe,
                self.config.connect_timeout,
                self.config.max_response_bytes,
            ) {
                Ok(value) => value,
                Err(_) => {
                    self.close_connection(&node.id);
                    continue;
                }
            };
            if !identity_matches(&probe_result, expected) {
                self.close_connection(&node.id);
                continue;
            }
            return Ok((node, stream));
        }

        Err(ClientError::NoVerifiedNode)
    }

    fn send_discovery(&self, message: &Message) -> Result<(), ClientError> {
        let payload = serde_json::to_vec(message)?;
        self.discovery_socket
            .send_to(&payload, self.config.multicast_endpoint)?;
        Ok(())
    }

    fn open_connection(&self, node_id: &str) -> Result<TcpStream, ClientError> {
        let listener = TcpListener::bind(SocketAddrV4::new(self.config.callback_address, 0))?;
        listener.set_nonblocking(true)?;
        let endpoint = match listener.local_addr()? {
            SocketAddr::V4(value) => value,
            SocketAddr::V6(_) => {
                return Err(ClientError::Configuration(
                    "TCP callback unexpectedly used IPv6".to_owned(),
                ));
            }
        };
        let open = Message::addressed(
            KIND_OPEN,
            &self.client_id,
            node_id,
            Some(json!({
                "command_ip": endpoint.ip().to_string(),
                "command_port": endpoint.port(),
            })),
        );
        let deadline = Instant::now() + self.config.connect_timeout;
        let mut next_request = Instant::now();
        loop {
            let now = Instant::now();
            if now >= deadline {
                return Err(ClientError::ConnectionTimeout);
            }
            if now >= next_request {
                self.send_discovery(&open)?;
                next_request = now + Duration::from_millis(250);
            }
            match listener.accept() {
                Ok((stream, peer)) => {
                    if !peer.ip().is_loopback() {
                        continue;
                    }
                    stream.set_nonblocking(false)?;
                    stream.set_nodelay(true)?;
                    return Ok(stream);
                }
                Err(error) if error.kind() == ErrorKind::WouldBlock => {
                    thread::sleep(Duration::from_millis(10));
                }
                Err(error) => return Err(error.into()),
            }
        }
    }

    fn close_connection(&self, node_id: &str) {
        let close = Message::addressed(KIND_CLOSE, &self.client_id, node_id, None);
        let _ = self.send_discovery(&close);
    }
}

fn create_discovery_socket(config: &ClientConfig) -> Result<UdpSocket, ClientError> {
    let socket = Socket::new(Domain::IPV4, Type::DGRAM, Some(Protocol::UDP))?;
    socket.set_reuse_address(true)?;
    socket.bind(
        &SocketAddr::V4(SocketAddrV4::new(
            config.multicast_bind_address,
            config.multicast_endpoint.port(),
        ))
        .into(),
    )?;
    let socket: UdpSocket = socket.into();
    socket.join_multicast_v4(
        config.multicast_endpoint.ip(),
        &config.multicast_bind_address,
    )?;
    socket.set_multicast_loop_v4(true)?;
    socket.set_multicast_ttl_v4(config.multicast_ttl)?;
    socket.set_read_timeout(Some(Duration::from_millis(50)))?;
    Ok(socket)
}

fn send_command(
    stream: &mut TcpStream,
    client_id: &str,
    node_id: &str,
    source: &str,
    timeout: Duration,
    max_response_bytes: usize,
) -> Result<CommandResult, ClientError> {
    let command = Message::addressed(
        KIND_COMMAND,
        client_id,
        node_id,
        Some(json!({
            "command": source,
            "unattended": true,
            "exec_mode": "ExecuteFile",
        })),
    );
    let payload = serde_json::to_vec(&command)?;
    stream.set_write_timeout(Some(timeout))?;
    stream.set_read_timeout(Some(timeout))?;
    stream.write_all(&payload).map_err(|error| {
        ClientError::OutcomeUnknown(format!("TCP command write failed: {error}"))
    })?;
    let message = read_message(stream, max_response_bytes)
        .map_err(|error| ClientError::OutcomeUnknown(format!("TCP result read failed: {error}")))?;
    command_result(message, client_id)
        .map_err(|error| ClientError::OutcomeUnknown(format!("invalid command result: {error}")))
}

fn read_message(stream: &mut TcpStream, max_bytes: usize) -> Result<Message, ClientError> {
    let mut bytes = Vec::with_capacity(8192);
    let mut chunk = [0_u8; 8192];
    loop {
        let size = stream.read(&mut chunk)?;
        if size == 0 {
            return Err(ClientError::OutcomeUnknown(
                "TCP connection closed before a complete JSON result".to_owned(),
            ));
        }
        if bytes.len().saturating_add(size) > max_bytes {
            return Err(ClientError::OutcomeUnknown(format!(
                "response exceeded {max_bytes} bytes"
            )));
        }
        bytes.extend_from_slice(&chunk[..size]);
        match serde_json::from_slice::<Message>(&bytes) {
            Ok(message) => return Ok(message),
            Err(error) if error.is_eof() => continue,
            Err(error) => return Err(ClientError::Json(error)),
        }
    }
}

fn identity_probe_source() -> String {
    format!(
        "import json, os, unreal\n\
         _p = os.path.realpath(os.path.abspath(unreal.Paths.project_dir()))\n\
         _v = {{'pid': os.getpid(), 'project_dir': _p}}\n\
         unreal.log({IDENTITY_MARKER:?} + json.dumps(_v, ensure_ascii=True))\n"
    )
}

#[derive(Deserialize)]
struct IdentityPayload {
    pid: u32,
    project_dir: String,
}

fn identity_matches(result: &CommandResult, expected: &ExpectedIdentity) -> bool {
    if !result.success {
        return false;
    }
    result.output.iter().any(|line| {
        let Some(position) = line.output.find(IDENTITY_MARKER) else {
            return false;
        };
        let payload = &line.output[position + IDENTITY_MARKER.len()..];
        let mut values =
            serde_json::Deserializer::from_str(payload.trim()).into_iter::<IdentityPayload>();
        let Some(Ok(actual)) = values.next() else {
            return false;
        };
        actual.pid == expected.pid
            && normalize_windows_path(&actual.project_dir)
                == normalize_windows_path(&expected.project_dir)
    })
}

fn normalize_windows_path(value: &str) -> String {
    let replaced = value.replace('/', "\\");
    replaced
        .strip_prefix("\\\\?\\")
        .unwrap_or(&replaced)
        .trim_end_matches('\\')
        .to_lowercase()
}

fn is_timeout(error: &std::io::Error) -> bool {
    matches!(error.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut)
}

#[cfg(test)]
mod tests {
    use std::net::TcpListener;

    use serde_json::json;

    use crate::protocol::{KIND_RESULT, OutputLine};

    use super::*;

    #[test]
    fn config_rejects_non_loopback_and_nonzero_ttl() {
        let config = ClientConfig {
            callback_address: Ipv4Addr::UNSPECIFIED,
            ..ClientConfig::default()
        };
        assert!(config.validate().is_err());
        let config = ClientConfig {
            multicast_ttl: 1,
            ..ClientConfig::default()
        };
        assert!(config.validate().is_err());
    }

    #[test]
    fn fragmented_tcp_json_is_reassembled() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let message = Message::addressed(
            KIND_RESULT,
            "editor-a",
            "client-a",
            Some(json!({"success": true, "result": "None", "output": []})),
        );
        let payload = serde_json::to_vec(&message).unwrap();
        let split = payload.len() / 2;
        let sender = thread::spawn(move || {
            let mut stream = TcpStream::connect(address).unwrap();
            stream.write_all(&payload[..split]).unwrap();
            thread::sleep(Duration::from_millis(20));
            stream.write_all(&payload[split..]).unwrap();
        });
        let (mut stream, _) = listener.accept().unwrap();
        let decoded = read_message(&mut stream, 4096).unwrap();
        sender.join().unwrap();
        assert_eq!(decoded, message);
    }

    #[test]
    fn identity_requires_pid_and_normalized_project() {
        let result = CommandResult {
            success: true,
            result: String::new(),
            output: vec![OutputLine {
                kind: "info".to_owned(),
                output: format!(
                    "prefix {IDENTITY_MARKER}{{\"pid\":42,\"project_dir\":\"X:/Fixture/ExampleProject/\"}}"
                ),
            }],
        };
        assert!(identity_matches(
            &result,
            &ExpectedIdentity {
                pid: 42,
                project_dir: r"x:\fixture\exampleproject".to_owned(),
            }
        ));
        assert!(!identity_matches(
            &result,
            &ExpectedIdentity {
                pid: 43,
                project_dir: r"x:\fixture\exampleproject".to_owned(),
            }
        ));
    }

    #[test]
    fn identity_accepts_log_suffix_after_json_value() {
        let result = CommandResult {
            success: true,
            result: String::new(),
            output: vec![OutputLine {
                kind: "info".to_owned(),
                output: format!(
                    "{IDENTITY_MARKER}{{\"pid\":42,\"project_dir\":\"X:/Fixture/ExampleProject\"}} [log]"
                ),
            }],
        };
        assert!(identity_matches(
            &result,
            &ExpectedIdentity {
                pid: 42,
                project_dir: r"X:\Fixture\ExampleProject".to_owned(),
            }
        ));
    }
}
