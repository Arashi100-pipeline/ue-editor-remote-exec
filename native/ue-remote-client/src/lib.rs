//! Independent client for Unreal Python Remote Execution.
//!
//! This crate is an interoperability implementation. It contains no Unreal
//! Engine source code and deliberately keeps process lifecycle outside the
//! protocol core.

pub mod client;
pub mod protocol;
