if(NOT DEFINED DUO_SOURCE_DIR OR NOT DEFINED DUO_OPTION_COMBINATION_BINARY_DIR)
    message(FATAL_ERROR "option-combination test requires source and binary directories")
endif()

execute_process(
    COMMAND "${CMAKE_COMMAND}" -S "${DUO_SOURCE_DIR}" -B "${DUO_OPTION_COMBINATION_BINARY_DIR}"
        -DDUO_NATIVE_TESTS=ON -DDUO_FUZZ_TESTS=ON
    RESULT_VARIABLE configure_result
    OUTPUT_VARIABLE configure_stdout
    ERROR_VARIABLE configure_stderr
)
set(configure_output "${configure_stdout}\n${configure_stderr}")

if(configure_result EQUAL 0)
    message(FATAL_ERROR "native and fuzz options unexpectedly configured together")
endif()
if(NOT configure_output MATCHES "DUO_NATIVE_TESTS and DUO_FUZZ_TESTS cannot be enabled together")
    message(FATAL_ERROR "option-combination rejection was not reported:\n${configure_output}")
endif()
if(configure_output MATCHES "CXX compiler identification" OR
   configure_output MATCHES "requires Clang with libFuzzer")
    message(FATAL_ERROR "option-combination guard ran after compiler-specific behavior:\n${configure_output}")
endif()
