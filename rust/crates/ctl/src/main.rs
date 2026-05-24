mod commands;
mod paths;
mod process;

fn main() -> anyhow::Result<()> {
    commands::run()
}
