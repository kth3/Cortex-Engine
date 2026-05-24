mod cli;
mod config;
mod idle;
mod protocol;
mod router;
mod worker;

fn main() -> anyhow::Result<()> {
    cli::run()
}
