fn main() {
    println!("{}", cortex_parsers::uuid5_for("test::foo"));
    println!("{}", cortex_parsers::uuid5_for("some/file.py::MyClass::my_method"));
}
