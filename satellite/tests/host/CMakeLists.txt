# tests/host/CMakeLists.txt — native host build for firmware regression tests.
#
# Standalone native C project. Never invoked by src/CMakeLists.txt's ESP-IDF
# idf_component_register() graph or by PlatformIO -- this is a plain
# gcc/clang + CMake build with zero ESP-IDF toolchain/target hardware
# involved. Invoke directly:
#
#   cmake -S tests/host -B tests/host/build
#   cmake --build tests/host/build
#   ctest --test-dir tests/host/build --output-on-failure
#
# See tests/host/README.md for platform-specific generator notes and what
# "pass" means for each target.

cmake_minimum_required(VERSION 3.16)
project(epm_host_tests C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()

set(REPO_ROOT ${CMAKE_CURRENT_SOURCE_DIR}/../..)

# ── scalar_stats anchor tests — pure-C mirrors of src/mic_task.c ───────────
add_executable(test_scalar_stats test_scalar_stats.c)
target_link_libraries(test_scalar_stats PRIVATE m)
target_compile_options(test_scalar_stats PRIVATE -Wall -Wextra)

# ── Hann coherent-gain test — links the REAL vendored esp-dsp source ───────
set(HANN_SRC ${REPO_ROOT}/managed_components/espressif__esp-dsp/modules/windows/hann/float/dsps_wind_hann_f32.c)
set(HANN_INC ${REPO_ROOT}/managed_components/espressif__esp-dsp/modules/windows/hann/include)
if(NOT EXISTS ${HANN_SRC})
  message(FATAL_ERROR
    "managed_components/ is not populated. It is .gitignore'd -- fetched by "
    "the ESP-IDF/PlatformIO component manager, not committed as source. Run "
    "`pio pkg install` (or any `pio run`) once from the repo root to fetch "
    "vendor dependencies (no flashing/hardware needed), then re-run cmake.")
endif()
add_executable(test_hann_window test_hann_window.c ${HANN_SRC})
target_include_directories(test_hann_window PRIVATE ${HANN_INC})
target_link_libraries(test_hann_window PRIVATE m)
target_compile_options(test_hann_window PRIVATE -Wall -Wextra)

# ── spectrum reduction test — links the real components/epm_dsp/spectrum.c ─
add_executable(test_spectrum
    ${CMAKE_CURRENT_SOURCE_DIR}/test_spectrum.c
    ${REPO_ROOT}/components/epm_dsp/spectrum.c
)
target_include_directories(test_spectrum PRIVATE
    ${REPO_ROOT}/components/epm_dsp/include
    ${REPO_ROOT}/src
)
target_link_libraries(test_spectrum PRIVATE m)
target_compile_options(test_spectrum PRIVATE -Wall -Wextra)

# ── envelope-analysis test — links the real components/epm_dsp/envelope.c ──
add_executable(test_envelope
    ${CMAKE_CURRENT_SOURCE_DIR}/test_envelope.c
    ${REPO_ROOT}/components/epm_dsp/envelope.c
)
target_include_directories(test_envelope PRIVATE ${REPO_ROOT}/components/epm_dsp/include)
target_link_libraries(test_envelope PRIVATE m)
target_compile_options(test_envelope PRIVATE -Wall -Wextra)

# ── Existing frame_encode test — wired in unmodified; its own raw-gcc
#    one-liner documented in its header comment still works standalone ─────
add_executable(test_frame_encode
    ${CMAKE_CURRENT_SOURCE_DIR}/test_frame_encode.c
    ${REPO_ROOT}/components/epm_codec/wire_protocol.c
    ${REPO_ROOT}/components/epm_codec/spectrum_codec.c
)
target_include_directories(test_frame_encode PRIVATE ${REPO_ROOT}/components/epm_codec/include)
target_link_libraries(test_frame_encode PRIVATE m)

# ── scalar-mapping test — links the real components/epm_codec/scalar_map.c ─
add_executable(test_scalar_map
    ${CMAKE_CURRENT_SOURCE_DIR}/test_scalar_map.c
    ${REPO_ROOT}/components/epm_codec/scalar_map.c
)
target_include_directories(test_scalar_map PRIVATE ${REPO_ROOT}/components/epm_codec/include)
target_link_libraries(test_scalar_map PRIVATE m)
target_compile_options(test_scalar_map PRIVATE -Wall -Wextra)

# ── STATUS_LED decode test — links the real components/epm_codec/wire_protocol.c ─
add_executable(test_status_led
    ${CMAKE_CURRENT_SOURCE_DIR}/test_status_led.c
    ${REPO_ROOT}/components/epm_codec/wire_protocol.c
)
target_include_directories(test_status_led PRIVATE ${REPO_ROOT}/components/epm_codec/include)
target_compile_options(test_status_led PRIVATE -Wall -Wextra)

# ── http_form_parse test — links the real components/epm_drivers/http_form_parse.c ─
add_executable(test_http_form_parse
    ${CMAKE_CURRENT_SOURCE_DIR}/test_http_form_parse.c
    ${REPO_ROOT}/components/epm_drivers/http_form_parse.c
)
target_include_directories(test_http_form_parse PRIVATE ${REPO_ROOT}/components/epm_drivers/include)
target_compile_options(test_http_form_parse PRIVATE -Wall -Wextra)

enable_testing()
add_test(NAME scalar_stats     COMMAND test_scalar_stats)
add_test(NAME hann_window      COMMAND test_hann_window)
add_test(NAME frame_encode     COMMAND test_frame_encode WORKING_DIRECTORY ${REPO_ROOT})
add_test(NAME spectrum         COMMAND test_spectrum)
add_test(NAME envelope         COMMAND test_envelope)
add_test(NAME scalar_map       COMMAND test_scalar_map)
add_test(NAME status_led       COMMAND test_status_led)
add_test(NAME http_form_parse  COMMAND test_http_form_parse)
