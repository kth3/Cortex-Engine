mod cli;
mod common;
mod index;
mod watch;

fn main() -> anyhow::Result<()> {
    cli::run()
}
