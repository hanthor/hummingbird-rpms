fn answer() -> i32 {
    42
}

fn main() {
    println!("rpmtest Rust works: {}", answer());
}

#[cfg(test)]
mod tests {
    #[test]
    fn answer_is_42() {
        assert_eq!(super::answer(), 42);
    }
}
