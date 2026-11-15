import length;

class Point {
    float x = 0.0;
    float y = 0.0;

    void translate(float dx, float dy) {
        x += dx;
        y += dy;
    }

    float length_squared() {
        return squared_length(x, y);
    }
}
