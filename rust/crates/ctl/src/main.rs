mod commands;
mod paths;
mod process;
mod relay;

fn main() -> anyhow::Result<()> {
    commands::run()
}
