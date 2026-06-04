mod commands;
mod paths;
mod process;
mod relay;

pub fn run() -> anyhow::Result<()> {
    commands::run()
}
