import d3d11;
import win32;

class Graphics {
    ID3D11Device* device = nullptr;
    ID3D11DeviceContext* context = nullptr;
    IDXGISwapChain* swap_chain = nullptr;
    ID3D11RenderTargetView* target = nullptr;
}

void release_graphics(Graphics* graphics) {
    if (graphics->context != nullptr) graphics->context->ClearState();
    if (graphics->target != nullptr) graphics->target->Release();
    if (graphics->swap_chain != nullptr) graphics->swap_chain->Release();
    if (graphics->context != nullptr) graphics->context->Release();
    if (graphics->device != nullptr) graphics->device->Release();
}

HRESULT create_graphics(HWND window, Graphics* graphics) {
    DXGI_SWAP_CHAIN_DESC description{};
    description.BufferDesc.Width = 800;
    description.BufferDesc.Height = 600;
    description.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    description.SampleDesc.Count = 1;
    description.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    description.BufferCount = 2;
    description.OutputWindow = window;
    description.Windowed = true;
    description.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;

    // WARP keeps the demo independent of a dedicated GPU.
    HRESULT result = D3D11CreateDeviceAndSwapChain(
        nullptr, D3D_DRIVER_TYPE_WARP, nullptr, 0,
        nullptr, 0, D3D11_SDK_VERSION, &description,
        &graphics->swap_chain, &graphics->device, nullptr, &graphics->context
    );
    if (FAILED(result)) return result;

    // Keep this demo windowed even if Alt+Enter is pressed.
    IDXGIFactory* factory = nullptr;
    result = graphics->swap_chain->GetParent(IID_PPV_ARGS(&factory));
    if (FAILED(result)) return result;
    result = factory->MakeWindowAssociation(window, DXGI_MWA_NO_ALT_ENTER);
    factory->Release();
    if (FAILED(result)) return result;

    ID3D11Texture2D* back_buffer = nullptr;
    result = graphics->swap_chain->GetBuffer(0, IID_PPV_ARGS(&back_buffer));
    if (FAILED(result)) return result;
    result = graphics->device->CreateRenderTargetView(back_buffer, nullptr, &graphics->target);
    back_buffer->Release();
    return result;
}

int render_window(HWND window, bool smoke_test) {
    Graphics graphics{};
    HRESULT result = create_graphics(window, &graphics);
    if (FAILED(result)) {
        print("D3D11 initialization failed:");
        print(result);
        release_graphics(&graphics);
        return 1;
    }

    float red[4] = {1.0, 0.0, 0.0, 1.0};
    MSG message{};
    bool running = true;
    int frames = 0;
    int exit_code = 0;
    ShowWindow(window, SW_SHOW);
    print("D3D11 red window ready. Close the window to exit.");

    while (running && IsWindow(window)) {
        while (PeekMessageA(&message, nullptr, 0, 0, PM_REMOVE)) {
            if (message.message == WM_QUIT) running = false;
            TranslateMessage(&message);
            DispatchMessageA(&message);
        }
        if (!running || !IsWindow(window)) break;
        if (IsIconic(window)) {
            Sleep(16);
            continue;
        }

        graphics.context->OMSetRenderTargets(1, &graphics.target, nullptr);
        graphics.context->ClearRenderTargetView(graphics.target, red);
        result = graphics.swap_chain->Present(1, 0);
        if (FAILED(result)) {
            print("Present failed:");
            print(result);
            exit_code = 2;
            break;
        }
        frames++;
        if (smoke_test && frames >= 3) break;
    }

    print("Presented red frames:");
    print(frames);
    release_graphics(&graphics);
    return exit_code;
}

int main() {
    HINSTANCE instance = GetModuleHandleA(nullptr);
    LPCSTR class_name = "SimpleCRedWindow";
    WNDCLASSA window_class{};
    window_class.lpfnWndProc = DefWindowProcA;
    window_class.hInstance = instance;
    window_class.hCursor = LoadCursorA(nullptr, IDC_ARROW);
    window_class.lpszClassName = class_name;
    if (RegisterClassA(&window_class) == 0) {
        print("Window class registration failed:");
        print(GetLastError());
        return 1;
    }

    // Fixed client size: resizing and swap-chain ResizeBuffers are a later step.
    DWORD style = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX;
    RECT bounds{0, 0, 800, 600};
    if (!AdjustWindowRect(&bounds, style, false)) {
        UnregisterClassA(class_name, instance);
        return 1;
    }
    HWND window = CreateWindowExA(
        0, class_name, "Simple C - D3D11 Red", style,
        CW_USEDEFAULT, CW_USEDEFAULT, bounds.right - bounds.left, bounds.bottom - bounds.top,
        nullptr, nullptr, instance, nullptr
    );
    if (window == nullptr) {
        print("Window creation failed:");
        print(GetLastError());
        UnregisterClassA(class_name, instance);
        return 1;
    }

    bool smoke_test = GetEnvironmentVariableA("SIMPLE_C_SMOKE_TEST", nullptr, 0) > 0;
    int exit_code = render_window(window, smoke_test);
    if (IsWindow(window)) DestroyWindow(window);
    UnregisterClassA(class_name, instance);
    return exit_code;
}
