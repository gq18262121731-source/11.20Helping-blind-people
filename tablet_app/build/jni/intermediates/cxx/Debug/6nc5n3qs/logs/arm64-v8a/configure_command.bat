@echo off
"D:\\development\\android-sdk\\cmake\\3.22.1\\bin\\cmake.exe" ^
  "-HC:\\Users\\袁昊\\AppData\\Local\\Pub\\Cache\\hosted\\pub.dev\\jni-1.1.2\\src" ^
  "-DCMAKE_SYSTEM_NAME=Android" ^
  "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON" ^
  "-DCMAKE_SYSTEM_VERSION=21" ^
  "-DANDROID_PLATFORM=android-21" ^
  "-DANDROID_ABI=arm64-v8a" ^
  "-DCMAKE_ANDROID_ARCH_ABI=arm64-v8a" ^
  "-DANDROID_NDK=D:\\development\\android-sdk\\ndk\\28.2.13676358" ^
  "-DCMAKE_ANDROID_NDK=D:\\development\\android-sdk\\ndk\\28.2.13676358" ^
  "-DCMAKE_TOOLCHAIN_FILE=D:\\development\\android-sdk\\ndk\\28.2.13676358\\build\\cmake\\android.toolchain.cmake" ^
  "-DCMAKE_MAKE_PROGRAM=D:\\development\\android-sdk\\cmake\\3.22.1\\bin\\ninja.exe" ^
  "-DCMAKE_LIBRARY_OUTPUT_DIRECTORY=D:\\工坊\\Morning-Star-main\\tablet_app\\build\\jni\\intermediates\\cxx\\Debug\\6nc5n3qs\\obj\\arm64-v8a" ^
  "-DCMAKE_RUNTIME_OUTPUT_DIRECTORY=D:\\工坊\\Morning-Star-main\\tablet_app\\build\\jni\\intermediates\\cxx\\Debug\\6nc5n3qs\\obj\\arm64-v8a" ^
  "-DCMAKE_BUILD_TYPE=Debug" ^
  "-BC:\\Users\\袁昊\\AppData\\Local\\Pub\\Cache\\hosted\\pub.dev\\jni-1.1.2\\android\\.cxx\\Debug\\6nc5n3qs\\arm64-v8a" ^
  -GNinja
