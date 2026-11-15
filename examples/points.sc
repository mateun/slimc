// Functions and classes may be used before their declarations.
int main() {
    Point original{3.0, 4.0};
    Point copy = original;
    copy.translate(1.0, 2.0);

    print("Original squared length:");
    print(original.length_squared());
    print("Copy squared length:");
    print(copy.length_squared());

    int total = 0;
    for (int i = 0; i < 5; i++) {
        total += i;
    }
    print("Sum from 0 through 4:");
    print(total);

    if (original.length_squared() != 25.0) {
        return 1;
    }
    if (copy.length_squared() != 52.0) {
        return 2;
    }

    Vec3 v1{1, 4, 8};
    Vec3 v2{4, 8, 7};
    Vec3 sum = v1 + v2;
    if (sum.x != 5 || sum.y != 12 || sum.z != 15) {
        return 3;
    }

    return 0;
}

class Vec3 {
    float x = 0;
    float y = 0;
    float z = 0;

    Vec3 op_+(Vec3 other) {
        return Vec3{x + other.x, y + other.y, z + other.z};
    }

}

class Point {
    float x = 0.0;
    float y = 0.0;

    void translate(float dx, float dy) {
        x += dx;
        y += dy;
    }

    float length_squared() {
        return square(x) + square(y);
    }
}

float square(float value) {
    return value * value;
}
