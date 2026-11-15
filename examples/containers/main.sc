import containers;

class Point {
    int x;
    int y;
}

int main() {
    Vector<int> numbers{};
    numbers.reserve(4);
    numbers.push_back(10);
    numbers.push_back(20);
    numbers.push_back(30);

    int total = 0;
    for (int i = 0; i < numbers.size(); i += 1) {
        total += numbers[i];
    }
    print(total); // 60

    Vector<int> copy = numbers;
    copy[0] = 99;
    copy.pop_back();
    print(numbers[0]); // 10: the original keeps its own elements.
    print(copy[0]);    // 99
    print(copy.size()); // 2

    Vector<Point> points{};
    points.push_back(Point{3, 4});
    print(points[0].x); // 3

    numbers.clear();
    print(numbers.empty()); // 1
    return 0;
}
