mod cli;
mod common;
mod index;
mod index_roots;
mod watch;

fn main() -> anyhow::Result<()> {
    cli::run()
}
