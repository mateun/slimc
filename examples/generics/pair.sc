class Pair<T> {
    T first;
    T second;

    T larger() {
        if (first > second) return first;
        return second;
    }

    Pair<T> op_+(Pair<T> other) {
        return Pair<T>{first + other.first, second + other.second};
    }
}
