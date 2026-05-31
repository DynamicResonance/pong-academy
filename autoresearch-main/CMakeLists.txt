cmake_minimum_required(VERSION 3.16)

project(autoresearch_controller LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(AUTORESEARCH_BUILD_CONTROLLER "AUTO" CACHE STRING "Build the Raspberry Pi controller executable: ON, OFF, or AUTO")
set_property(CACHE AUTORESEARCH_BUILD_CONTROLLER PROPERTY STRINGS ON OFF AUTO)

add_library(command_parser
    src/command.cpp
)
target_include_directories(command_parser PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)
target_compile_options(command_parser PRIVATE -Wall -Wextra -Wpedantic)

add_executable(command_parser_test
    tests/command_parser_test.cpp
)
target_link_libraries(command_parser_test PRIVATE command_parser)
target_compile_options(command_parser_test PRIVATE -Wall -Wextra -Wpedantic)

enable_testing()
add_test(NAME command_parser_test COMMAND command_parser_test)

find_path(PIGPIOD_IF2_INCLUDE_DIR pigpiod_if2.h)
find_library(PIGPIOD_IF2_LIBRARY pigpiod_if2)
find_library(RT_LIBRARY rt)
find_package(Threads REQUIRED)
find_package(zenohc QUIET)

set(PIGPIO_SYSTEM_LIBS "")
if(RT_LIBRARY)
    list(APPEND PIGPIO_SYSTEM_LIBS ${RT_LIBRARY})
endif()

set(CONTROLLER_DEPS_FOUND FALSE)
if(PIGPIOD_IF2_INCLUDE_DIR AND PIGPIOD_IF2_LIBRARY AND TARGET zenohc::lib)
    set(CONTROLLER_DEPS_FOUND TRUE)
endif()

if((AUTORESEARCH_BUILD_CONTROLLER STREQUAL "ON") AND NOT CONTROLLER_DEPS_FOUND)
    message(FATAL_ERROR "Controller dependencies not found. Install libpigpiod-if-dev and libzenohc-dev, or configure with -DAUTORESEARCH_BUILD_CONTROLLER=OFF.")
endif()

if(CONTROLLER_DEPS_FOUND AND NOT (AUTORESEARCH_BUILD_CONTROLLER STREQUAL "OFF"))
    add_executable(servo_controller
        src/controller.cpp
        src/servo_pwm.cpp
    )
    target_include_directories(servo_controller PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/include
        ${PIGPIOD_IF2_INCLUDE_DIR}
    )
    target_link_libraries(servo_controller PRIVATE
        command_parser
        ${PIGPIOD_IF2_LIBRARY}
        ${PIGPIO_SYSTEM_LIBS}
        zenohc::lib
        Threads::Threads
    )
    target_compile_options(servo_controller PRIVATE -Wall -Wextra -Wpedantic)
else()
    message(STATUS "Skipping servo_controller; libpigpiod-if-dev and/or libzenohc-dev were not found.")
endif()
