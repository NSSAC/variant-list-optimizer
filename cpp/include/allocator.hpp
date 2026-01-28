#pragma once

#include <cstdint>
#include <cstdlib>
#include <new>
#include <type_traits>

namespace hpc_utils {

constexpr int L1_CACHE_SIZE = 64;

extern std::int64_t TOTAL_ALLOC;
extern std::int64_t MAX_ALLOC;

template <typename Type>
struct aligned_allocator {
    using value_type = Type;
    using propagate_on_container_move_assignment = std::true_type;
    using is_always_equal = std::true_type;

    aligned_allocator() = default;
    aligned_allocator(const aligned_allocator&) = default;
    aligned_allocator(aligned_allocator&&) = default;
    aligned_allocator& operator=(const aligned_allocator&) = default;
    aligned_allocator& operator=(aligned_allocator&&) = default;

    template <typename OtherType>
    aligned_allocator(const aligned_allocator<OtherType>&) noexcept {}

    Type* allocate(std::size_t n) {
        std::int64_t alloc_size = n * sizeof(Type);
        auto rem = alloc_size % L1_CACHE_SIZE;
        if (rem) {
            alloc_size += L1_CACHE_SIZE - rem;
        }

        void* ret = std::aligned_alloc(L1_CACHE_SIZE, alloc_size);
        if (ret == nullptr) {
            throw std::bad_alloc();
        }

        #pragma omp atomic update
        TOTAL_ALLOC += alloc_size;

        #pragma omp atomic compare
        MAX_ALLOC = MAX_ALLOC < TOTAL_ALLOC ? TOTAL_ALLOC : MAX_ALLOC;

        return static_cast<Type*>(ret);
    }

    void deallocate(Type* p, std::size_t n = 0) {
        std::int64_t alloc_size = n * sizeof(Type);
        auto rem = alloc_size % L1_CACHE_SIZE;
        if (rem) {
            alloc_size += L1_CACHE_SIZE - rem;
        }

        if (p != nullptr) {
            std::free(p);
        }

        #pragma omp atomic update
        TOTAL_ALLOC -= alloc_size;
    }

    bool operator==(const aligned_allocator<Type>&) const noexcept {
        return true;
    }
    bool operator!=(const aligned_allocator<Type>&) const noexcept {
        return true;
    }
};

} // namespace hpc_utils
