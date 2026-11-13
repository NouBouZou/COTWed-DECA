cmake_minimum_required(VERSION 3.5)

project(deca_cpp C)

# Optional native build of the texture decoder (cpp/process_image). DECA falls back to its Python decoders
# when process_image.dll / process_image.so is not found (see python/deca/deca/dxgi.py).

if(NOT CMAKE_C_COMPILER_ID MATCHES "MSVC")
set(CMAKE_C_FLAGS_DEBUG "-g")
set(CMAKE_C_FLAGS_RELEASE "-O3")
endif()

add_subdirectory(cpp)
