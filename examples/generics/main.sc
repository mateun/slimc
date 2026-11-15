import pair;
import algorithms;

class Box<T> {
    T value;

    T get() {
        return value;
    }
}

int main() {
    Pair<int> numbers{3, 7};
    Pair<int> added = numbers + Pair<int>{10, 20};
    print(numbers.larger());
    print(added.first);
    print(added.second);
    print(max<float>(1.5, 2.5));

    Box<Pair<int>> boxed{numbers};
    Pair<int> copy = identity<Pair<int>>(boxed.get());
    copy.first = 99;
    print(boxed.value.first);
    print(copy.first);
    return 0;
}
