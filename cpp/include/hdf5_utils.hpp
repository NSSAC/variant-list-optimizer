#pragma once

#include <cstdint>
#include <array>

#include <static_array.hpp>

#include <H5Cpp.h>

namespace hpc_utils {

template <typename Type>
const H5::PredType& h5_type() = delete;

template <>
inline const H5::PredType& h5_type<std::int8_t>() {
    return H5::PredType::NATIVE_INT8;
}
template <>
inline const H5::PredType& h5_type<std::int16_t>() {
    return H5::PredType::NATIVE_INT16;
}
template <>
inline const H5::PredType& h5_type<std::int32_t>() {
    return H5::PredType::NATIVE_INT32;
}
template <>
inline const H5::PredType& h5_type<std::int64_t>() {
    return H5::PredType::NATIVE_INT64;
}

template <>
inline const H5::PredType& h5_type<std::uint8_t>() {
    return H5::PredType::NATIVE_UINT8;
}
template <>
inline const H5::PredType& h5_type<std::uint16_t>() {
    return H5::PredType::NATIVE_UINT16;
}
template <>
inline const H5::PredType& h5_type<std::uint32_t>() {
    return H5::PredType::NATIVE_UINT32;
}
template <>
inline const H5::PredType& h5_type<std::uint64_t>() {
    return H5::PredType::NATIVE_UINT64;
}

template <>
inline const H5::PredType& h5_type<float>() {
    return H5::PredType::NATIVE_FLOAT;
}
template <>
inline const H5::PredType& h5_type<double>() {
    return H5::PredType::NATIVE_DOUBLE;
}

template <typename Vec, typename Group>
void read_dataset(const Group& group, const std::string& name, Vec& arr) {
    std::array<hsize_t, 1> dims = {std::ssize(arr)};

    auto dataspace = H5::DataSpace(dims.size(), dims.data());
    auto datatype = h5_type<typename Vec::value_type>();
    auto dataset = group.openDataSet(name);

    dataset.read(arr.data(), datatype, dataspace);
}

template <typename T, typename Group>
void read_dataset(const Group& group, const std::string& name, StaticArray2d<T>& arr) {
    std::array<hsize_t, 2> dims = {hsize_t(arr.rows()), hsize_t(arr.cols())};

    auto dataspace = H5::DataSpace(dims.size(), dims.data());
    auto datatype = h5_type<T>();
    auto dataset = group.openDataSet(name);

    dataset.read(arr.data(), datatype, dataspace);
}

template <typename T, typename Group>
T read_attribute(const Group& group, const std::string& name) {
    T attr;

    auto datatype = h5_type<T>();
    auto attribute = group.openAttribute(name);

    attribute.read(datatype, &attr);
    return attr;
}

template <typename Vec, typename Group>
void write_dataset(Vec& arr, Group& group, const std::string& name) {
    std::array<hsize_t, 1> dims = {std::ssize(arr)};

    auto dataspace = H5::DataSpace(dims.size(), dims.data());
    auto datatype = h5_type<typename Vec::value_type>();
    auto dataset = group.createDataSet(name, datatype, dataspace);
    dataset.write(arr.data(), datatype);
}

template <typename T, typename Group>
void write_dataset(StaticArray2d<T>& arr, Group& group, const std::string& name) {
    std::array<hsize_t, 2> dims = {hsize_t(arr.rows()), hsize_t(arr.cols())};

    auto dataspace = H5::DataSpace(dims.size(), dims.data());
    auto datatype = h5_type<T>();
    auto dataset = group.createDataSet(name, datatype, dataspace);
    dataset.write(arr.data(), datatype);
}

template <typename T, typename Group>
void write_attribute(const T attr, Group& group, const std::string& name) {
    auto dataspace = H5::DataSpace(H5S_SCALAR);
    auto datatype = h5_type<T>();
    auto attribute = group.createAttribute(name, datatype, dataspace);
    attribute.write(datatype, &attr);
}

} // namespace hpc_utils
