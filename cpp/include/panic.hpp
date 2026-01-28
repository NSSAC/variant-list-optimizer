#pragma once

#include <fmt/format.h>

template <typename... Type>
void _panic(const char* file, const int line, fmt::format_string<Type...> fmt, Type&&... args) {
    auto err = fmt::format(fmt, std::forward<Type>(args)...);
    throw std::runtime_error(fmt::format("Error {}:{}: {}", file, line, err));
}

#define panic(fmt, ...) _panic(__FILE__, __LINE__, fmt __VA_OPT__(, ) __VA_ARGS__)
