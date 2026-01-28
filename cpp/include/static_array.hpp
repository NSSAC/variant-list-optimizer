#pragma once

#include <utility>

#include <allocator.hpp>

namespace hpc_utils {

template <typename Type, typename Allocator = aligned_allocator<Type>>
struct StaticArray {
    using value_type = Type;
    using allocator_type = Allocator;
    using size_type = std::int64_t;

    Type* _data;
    size_type _size;
    Allocator _allocator;

    explicit StaticArray(size_type size)
        : _data{nullptr}
        , _size{0}
        , _allocator{} {
        _data = _allocator.allocate(size);
        _size = size;
    }

    ~StaticArray() {
        if (_data != nullptr) {
            _allocator.deallocate(_data, _size);
            _data = nullptr;
            _size = 0;
        }
    }

    StaticArray() = delete;                              // default constructor
    StaticArray(const StaticArray&) = delete;            // copy constructor
    StaticArray(StaticArray&&) = default;                // move constructor
    StaticArray& operator=(const StaticArray&) = delete; // copy assignment
    StaticArray& operator=(StaticArray&&) = default;     // move assignment

    [[nodiscard]] Type& operator[](size_type i) {
        return _data[i];
    }
    [[nodiscard]] Type operator[](size_type i) const {
        return _data[i];
    }

    [[nodiscard]] Type* begin() const {
        return _data;
    }
    [[nodiscard]] Type* end() const {
        return _data + _size;
    }
    [[nodiscard]] size_type size() const {
        return _size;
    }
    [[nodiscard]] Type* data() const {
        return _data;
    }

    template <typename... Args>
    void construct(size_type i, Args&&... args) {
        new (&_data[i]) Type(std::forward<Args>(args)...);
    }
    void destruct(size_type i) {
        _data[i].~Type();
    }
};

template <typename Type, typename Allocator = aligned_allocator<Type>>
struct StaticArray2d {
    using value_type = Type;
    using allocator_type = Allocator;
    using size_type = std::int64_t;

    size_type _rows;
    size_type _cols;
    StaticArray<Type, Allocator> _array;

    explicit StaticArray2d(size_type rows, size_type cols)
        : _rows{rows}
        , _cols{cols}
        , _array{rows * cols} {}

    StaticArray2d() = delete;                                // default constructor
    StaticArray2d(const StaticArray2d&) = delete;            // copy constructor
    StaticArray2d(StaticArray2d&&) = default;                // move constructor
    StaticArray2d& operator=(const StaticArray2d&) = delete; // copy assignment
    StaticArray2d& operator=(StaticArray2d&&) = default;     // move assignment

    [[nodiscard]] Type& operator[](size_type i, size_type j) {
        return _array[i * _cols + j];
    }
    [[nodiscard]] Type operator[](size_type i, size_type j) const {
        return _array[i * _cols + j];
    }

    [[nodiscard]] Type* begin() const {
        return _array.begin();
    }
    [[nodiscard]] Type* end() const {
        return _array.end();
    }
    [[nodiscard]] size_type size() const {
        return _array.size();
    }
    [[nodiscard]] Type* data() const {
        return _array.data();
    }

    template <typename... Args>
    void construct(size_type i, size_type j, Args&&... args) {
        new (&_array[i * _cols + j]) Type(std::forward<Args>(args)...);
    }
    void destruct(size_type i, size_type j) {
        _array[i * _cols + j].~Type();
    }

    [[nodiscard]] size_type rows() const {
        return _rows;
    }
    [[nodiscard]] size_type cols() const {
        return _cols;
    }
};

} // namespace hpc_utils
