cmake_minimum_required(VERSION 3.20)
project(hdrg_distance_five LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(verilator REQUIRED HINTS $ENV{VERILATOR_ROOT})

add_executable(hdrg_exhaustive exhaustive.cpp)
target_compile_definitions(hdrg_exhaustive PRIVATE VL_TIME_CONTEXT)
verilate(hdrg_exhaustive PREFIX Vhdrg_online
    VERILATOR_ARGS --top-module hdrg_online -GD=5 -Wno-fatal
    SOURCES ../../rtl/hdrg_online/hdrg_online.sv)

enable_testing()
add_test(NAME all_component_syndromes COMMAND hdrg_exhaustive)
