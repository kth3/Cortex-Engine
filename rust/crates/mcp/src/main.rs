mod catalog;
mod hooks;
mod protocol;
mod storage_tools;

fn main() -> std::io::Result<()> {
    protocol::run_stdio(std::io::stdin().lock(), std::io::stdout().lock())
}
