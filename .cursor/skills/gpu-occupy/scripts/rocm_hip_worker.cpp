#include <hip/hip_runtime.h>

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

#if defined(__linux__)
#include <sys/prctl.h>
#endif

__global__ void busy_kernel(float* a, float* b, float* c, size_t n, int inner) {
    size_t idx = blockIdx.x * blockDim.x + threadIdx.x;
    size_t stride = blockDim.x * gridDim.x;
    for (size_t i = idx; i < n; i += stride) {
        float x = a[i];
        float y = b[i];
        #pragma unroll 4
        for (int j = 0; j < inner; ++j) {
            x = x * 1.000001f + y;
            y = y * 0.999999f + x;
        }
        c[i] = x + y;
    }
}

static void check(hipError_t err, const char* what) {
    if (err != hipSuccess) {
        std::fprintf(stderr, "%s: %s\n", what, hipGetErrorString(err));
        std::exit(1);
    }
}

int main(int argc, char** argv) {
    const char* env_gpu = std::getenv("SG_GPU");
    const char* env_fraction = std::getenv("SG_FRACTION");
    const char* env_title = std::getenv("SG_TITLE");

    int gpu = 0;
    double fraction = 0.80;
    std::string title = argv[0];
    if (argc >= 4) {
        gpu = std::atoi(argv[1]);
        fraction = std::atof(argv[2]);
        title = argv[3];
    } else if (env_gpu != nullptr) {
        gpu = std::atoi(env_gpu);
        if (env_fraction != nullptr) {
            fraction = std::atof(env_fraction);
        }
        if (env_title != nullptr) {
            title = env_title;
        }
    } else {
        std::fprintf(
            stderr,
            "usage: %s <gpu> <fraction> <title>, or set SG_GPU/SG_FRACTION/SG_TITLE\n",
            argv[0]
        );
        return 2;
    }

#if defined(__linux__)
    prctl(PR_SET_NAME, title.c_str(), 0, 0, 0);
#endif

    check(hipSetDevice(gpu), "hipSetDevice");

    size_t free_bytes = 0;
    size_t total_bytes = 0;
    check(hipMemGetInfo(&free_bytes, &total_bytes), "hipMemGetInfo");

    const size_t mib = 1024ULL * 1024ULL;
    const size_t gib = 1024ULL * mib;
    size_t target = static_cast<size_t>(total_bytes * fraction);
    size_t headroom = gib;

    const size_t n = 64ULL * 1024ULL * 1024ULL;
    float *a = nullptr, *b = nullptr, *c = nullptr;
    check(hipMalloc(&a, n * sizeof(float)), "hipMalloc a");
    check(hipMalloc(&b, n * sizeof(float)), "hipMalloc b");
    check(hipMalloc(&c, n * sizeof(float)), "hipMalloc c");
    check(hipMemset(a, 1, n * sizeof(float)), "hipMemset a");
    check(hipMemset(b, 2, n * sizeof(float)), "hipMemset b");

    std::vector<void*> pads;
    size_t chunk = gib;
    while (true) {
        check(hipMemGetInfo(&free_bytes, &total_bytes), "hipMemGetInfo loop");
        size_t used = total_bytes - free_bytes;
        if (used >= target || free_bytes <= headroom + 256ULL * mib) {
            break;
        }
        size_t want = target - used;
        if (want > chunk) {
            want = chunk;
        }
        if (want > free_bytes - headroom) {
            want = free_bytes - headroom;
        }
        void* ptr = nullptr;
        hipError_t err = hipMalloc(&ptr, want);
        if (err == hipSuccess) {
            pads.push_back(ptr);
            continue;
        }
        if (want <= 256ULL * mib) {
            break;
        }
        chunk = want / 2;
    }

    check(hipMemGetInfo(&free_bytes, &total_bytes), "hipMemGetInfo final");
    size_t used = total_bytes - free_bytes;
    std::printf(
        "%s GPU%d: reserved %.1fGB / total %.1fGB (%.0f%%), pads=%zu\n",
        title.c_str(),
        gpu,
        used / 1024.0 / 1024.0 / 1024.0,
        total_bytes / 1024.0 / 1024.0 / 1024.0,
        used * 100.0 / total_bytes,
        pads.size()
    );
    std::fflush(stdout);

    while (true) {
        hipLaunchKernelGGL(busy_kernel, dim3(4096), dim3(256), 0, 0, a, b, c, n, 256);
        check(hipGetLastError(), "busy_kernel launch");
        check(hipDeviceSynchronize(), "hipDeviceSynchronize");
    }
}
