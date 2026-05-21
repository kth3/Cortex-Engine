//! Cortex 파일 스캐너 — Python `cortex/scanner/` 대응.
//!
//! 워크스페이스를 순회하며 인덱싱 대상 파일을 수집한다.
//! `.gitignore` 및 블랙리스트 디렉토리, 확장자 화이트리스트로 필터링.

pub mod filters;
pub mod finder;
pub mod ignores;
pub mod index_roots;
pub mod settings;

pub use filters::{is_supported_extension, should_include, SUPPORTED_EXTENSIONS};
pub use finder::scan_files;
pub use ignores::{load_gitignore, should_ignore, DEFAULT_IGNORES};
pub use index_roots::{normalize_configured_index_roots, IndexRoot};
pub use settings::{load_settings, settings_paths};
