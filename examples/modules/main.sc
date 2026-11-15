import geometry.point;
import math;

int main() {
    Point original{3.0, 4.0};
    Point copy = original;
    copy.translate(1.0, 2.0);

    print("Original squared length:");
    print(original.length_squared());
    print("Copy squared length:");
    print(copy.length_squared());
    print("Square of 5:");
    print(square(5.0));
    return 0;
}
