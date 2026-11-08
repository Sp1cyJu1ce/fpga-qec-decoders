cmake_minimum_required(VERSION 3.20)
project(hybrid_hdrg LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(DISTANCE 5 CACHE STRING "Spatial code distance")
find_package(verilator REQUIRED HINTS $ENV{VERILATOR_ROOT})
add_executable(hybrid_hdrg main.cpp)
target_compile_definitions(hybrid_hdrg PRIVATE VL_TIME_CONTEXT)
verilate(hybrid_hdrg PREFIX Vtb_hybrid_hdrg
    VERILATOR_ARGS --timing --top-module tb_hybrid_hdrg -GD=${DISTANCE} -Wno-fatal
    SOURCES tb_hybrid_hdrg.sv
            ../../rtl/hybrid_hdrg/cnn_hdrg.sv ../../rtl/hdrg_stream/hdrg_stream.sv
            ../../rtl/arty_2d/decoder_top_v2.v ../../rtl/arty_2d/conv_engine_v2.v
            ../../rtl/arty_2d/qec_mem.v)
