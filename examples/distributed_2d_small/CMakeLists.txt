cmake_minimum_required(VERSION 3.20)
project(distributed_example LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(verilator REQUIRED HINTS $ENV{VERILATOR_ROOT})

get_filename_component(REPO "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
add_executable(simulation sim_main.cpp)
if(MSVC)
    target_compile_options(simulation PRIVATE /bigobj /MP2)
endif()
verilate(simulation PREFIX Vtb
    VERILATOR_ARGS --top-module tb --timing --assert -Wno-fatal
                   --output-split 10000 --output-split-cfuncs 1000
                   --x-initial unique --x-assign unique
    SOURCES
        ${REPO}/rtl/distributed_2d/banked_raster_feeder.sv
        ${REPO}/rtl/distributed_2d/retained_layer_stage.sv
        ${REPO}/rtl/distributed_2d/transport_async_fifo.sv
        ${REPO}/rtl/distributed_2d/tile_board.sv
        ${REPO}/rtl/hardwired_2d/hardwired_L1.v
        ${REPO}/rtl/hardwired_2d/hardwired_L2.v
        ${REPO}/rtl/hardwired_2d/hardwired_L3.v
        tb.sv)

# The full four-worker model has a large shared declaration header. Compile
# it once rather than parsing it again for every generated translation unit.
target_precompile_headers(simulation PRIVATE
    "${CMAKE_CURRENT_BINARY_DIR}/CMakeFiles/simulation.dir/Vtb.dir/Vtb__pch.h")
set_source_files_properties(sim_main.cpp PROPERTIES SKIP_PRECOMPILE_HEADERS ON)
foreach(runtime verilated.cpp verilated_dpi.cpp verilated_timing.cpp verilated_threads.cpp)
    set_source_files_properties("${VERILATOR_ROOT}/include/${runtime}"
        PROPERTIES SKIP_PRECOMPILE_HEADERS ON)
endforeach()
