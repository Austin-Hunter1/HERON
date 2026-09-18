# Build the HERON recorder.
#   cd Onboard_Software/recorder
#   cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
#   cmake --build build -j
#   sudo cmake --install build      # puts heron_recorder in /usr/local/bin
cmake_minimum_required(VERSION 3.16)
project(heron_recorder CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# UHD 4.x supports the B210 and the B200mini/B206mini (D-020).
find_package(UHD 4.0 REQUIRED)
find_package(Boost 1.65 REQUIRED COMPONENTS program_options)
find_package(Threads REQUIRED)

add_executable(heron_recorder heron_recorder.cpp)
target_include_directories(heron_recorder PRIVATE ${UHD_INCLUDE_DIRS} ${Boost_INCLUDE_DIRS})
target_link_libraries(heron_recorder PRIVATE ${UHD_LIBRARIES} Boost::program_options Threads::Threads)
target_compile_options(heron_recorder PRIVATE -Wall -Wextra -O2)

install(TARGETS heron_recorder RUNTIME DESTINATION bin)
