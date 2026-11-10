cmake_minimum_required(VERSION 3.20)
project(hybrid_hdrg LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(DISTANCE 5 CACHE STRING "Spatial code distance")
set(CNN_CORE "banked" CACHE STRING "CNN that supplies the local correction")
if(CNN_CORE STREQUAL "resident")
    set(testbench tb_resident_hdrg)
    set(core_sources
        ../../rtl/hybrid_hdrg/resident_hdrg.sv
        ../../rtl/resident_2d/worker_rowready.sv
        ../../rtl/resident_2d/retained_layer_stage.sv
        ../../rtl/resident_2d/banked_raster_feeder.sv
        ../../rtl/hardwired_2d/hardwired_L1.v
        ../../rtl/hardwired_2d/hardwired_L2.v
        ../../rtl/hardwired_2d/hardwired_L3.v)
elseif(CNN_CORE STREQUAL "banked")
    set(testbench tb_hybrid_hdrg)
    set(core_sources
        ../../rtl/hybrid_hdrg/cnn_hdrg.sv
        ../../rtl/arty_2d/decoder_top_v2.v ../../rtl/arty_2d/conv_engine_v2.v
        ../../rtl/arty_2d/qec_mem.v)
else()
    message(FATAL_ERROR "CNN_CORE must be banked or resident")
endif()
find_package(verilator REQUIRED HINTS $ENV{VERILATOR_ROOT})
add_executable(hybrid_hdrg main.cpp)
target_compile_definitions(hybrid_hdrg PRIVATE VL_TIME_CONTEXT)
verilate(hybrid_hdrg PREFIX Vtb_hybrid_hdrg
    VERILATOR_ARGS --timing --top-module ${testbench} -GD=${DISTANCE} -Wno-fatal
    SOURCES ${testbench}.sv ../../rtl/hdrg_stream/hdrg_stream.sv ${core_sources})
