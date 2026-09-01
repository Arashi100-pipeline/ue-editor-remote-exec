use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const PROTOCOL_VERSION: u32 = 1;
pub const PROTOCOL_MAGIC: &str = "ue_py";

pub const KIND_PING: &str = "ping";
pub const KIND_PONG: &str = "pong";
pub const KIND_OPEN: &str = "open_connection";
pub const KIND_CLOSE: &str = "close_connection";
pub const KIND_COMMAND: &str = "command";
pub const KIND_RESULT: &str = "command_result";

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct Message {
    pub version: u32,
    pub magic: String,
    #[serde(rename = "type")]
    pub kind: String,
    pub source: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub dest: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<Value>,
}

impl Message {
    pub fn new(kind: impl Into<String>, source: impl Into<String>) -> Self {
        Self {
            version: PROTOCOL_VERSION,
            magic: PROTOCOL_MAGIC.to_owned(),
            kind: kind.into(),
            source: source.into(),
            dest: None,
            data: None,
        }
    }

    pub fn addressed(
        kind: impl Into<String>,
        source: impl Into<String>,
        dest: impl Into<String>,
        data: Option<Value>,
    ) -> Self {
        Self {
            dest: Some(dest.into()),
            data,
            ..Self::new(kind, source)
        }
    }

    pub fn validate_for(&self, local_id: &str) -> Result<(), ProtocolError> {
        if self.version != PROTOCOL_VERSION {
            return Err(ProtocolError::Version(self.version));
        }
        if self.magic != PROTOCOL_MAGIC {
            return Err(ProtocolError::Magic(self.magic.clone()));
        }
        if self.source.is_empty() || self.source == local_id {
            return Err(ProtocolError::Source);
        }
        if self.dest.as_deref().is_some_and(|dest| dest != local_id) {
            return Err(ProtocolError::Destination);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct OutputLine {
    #[serde(rename = "type", default)]
    pub kind: String,
    #[serde(default)]
    pub output: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct CommandResult {
    pub success: bool,
    #[serde(default)]
    pub result: String,
    #[serde(default)]
    pub output: Vec<OutputLine>,
}

#[derive(Debug, thiserror::Error)]
pub enum ProtocolError {
    #[error("unsupported protocol version {0}")]
    Version(u32),
    #[error("invalid protocol magic {0:?}")]
    Magic(String),
    #[error("invalid message source")]
    Source,
    #[error("message is addressed to another client")]
    Destination,
    #[error("unexpected message kind {actual:?}, expected {expected:?}")]
    Kind {
        actual: String,
        expected: &'static str,
    },
    #[error("message has no data object")]
    MissingData,
    #[error("invalid command result: {0}")]
    InvalidResult(#[from] serde_json::Error),
}

pub fn command_result(message: Message, local_id: &str) -> Result<CommandResult, ProtocolError> {
    message.validate_for(local_id)?;
    if message.kind != KIND_RESULT {
        return Err(ProtocolError::Kind {
            actual: message.kind,
            expected: KIND_RESULT,
        });
    }
    let data = message.data.ok_or(ProtocolError::MissingData)?;
    Ok(serde_json::from_value(data)?)
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn message_round_trip_preserves_unicode() {
        let value = Message::addressed(
            KIND_COMMAND,
            "client-a",
            "editor-a",
            Some(json!({"command": "print('你好')"})),
        );
        let encoded = serde_json::to_vec(&value).unwrap();
        let decoded: Message = serde_json::from_slice(&encoded).unwrap();
        assert_eq!(decoded, value);
    }

    #[test]
    fn rejects_wrong_magic_and_destination() {
        let mut value = Message::addressed(KIND_PONG, "editor-a", "client-b", None);
        assert!(matches!(
            value.validate_for("client-a"),
            Err(ProtocolError::Destination)
        ));
        value.dest = Some("client-a".to_owned());
        value.magic = "not-ue".to_owned();
        assert!(matches!(
            value.validate_for("client-a"),
            Err(ProtocolError::Magic(value)) if value == "not-ue"
        ));
    }

    #[test]
    fn parses_typed_command_result() {
        let message = Message::addressed(
            KIND_RESULT,
            "editor-a",
            "client-a",
            Some(json!({
                "success": true,
                "result": "None",
                "output": [{"type": "info", "output": "done"}]
            })),
        );
        let result = command_result(message, "client-a").unwrap();
        assert!(result.success);
        assert_eq!(result.output[0].output, "done");
    }
}
