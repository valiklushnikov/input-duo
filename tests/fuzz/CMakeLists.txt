set(DUO_FUZZ_SOURCES
    fuzz_cdc_frame.cpp
    fuzz_spi_frame.cpp
    fuzz_config.cpp
)

function(duo_add_corpus_smoke name source)
    set(target "duo_fuzz_${name}_corpus_smoke")
    add_executable(${target}
        corpus_smoke.cpp
        ${source}
    )
    target_link_libraries(${target} PRIVATE duo_common)
    target_compile_definitions(${target} PRIVATE
        DUO_FUZZ_CORPUS_DIR="${CMAKE_CURRENT_SOURCE_DIR}/corpus/${name}"
    )
    if(MSVC)
        target_compile_options(${target} PRIVATE /EHsc)
    endif()
    add_test(NAME fuzz_corpus_${name} COMMAND ${target})
endfunction()

if(BUILD_TESTING AND DUO_NATIVE_TESTS AND NOT DUO_FUZZ_TESTS)
    duo_add_corpus_smoke(cdc fuzz_cdc_frame.cpp)
    duo_add_corpus_smoke(spi fuzz_spi_frame.cpp)
    duo_add_corpus_smoke(config fuzz_config.cpp)
endif()

if(DUO_FUZZ_TESTS)
    if(NOT CMAKE_CXX_COMPILER_ID STREQUAL "Clang")
        message(FATAL_ERROR
            "DUO_FUZZ_TESTS requires Clang with libFuzzer; configure with "
            "-DCMAKE_CXX_COMPILER=clang++ and a Clang toolchain that supports "
            "-fsanitize=fuzzer,address,undefined.")
    endif()

    set(DUO_FUZZ_SANITIZER_FLAGS
        -fsanitize=fuzzer,address,undefined
        -fno-omit-frame-pointer
        -g
    )
    target_compile_options(duo_common PRIVATE ${DUO_FUZZ_SANITIZER_FLAGS})

    foreach(source IN LISTS DUO_FUZZ_SOURCES)
        get_filename_component(name "${source}" NAME_WE)
        add_executable(duo_${name} ${source})
        target_link_libraries(duo_${name} PRIVATE duo_common)
        target_compile_options(duo_${name} PRIVATE ${DUO_FUZZ_SANITIZER_FLAGS})
        target_link_options(duo_${name} PRIVATE ${DUO_FUZZ_SANITIZER_FLAGS})
    endforeach()
endif()
