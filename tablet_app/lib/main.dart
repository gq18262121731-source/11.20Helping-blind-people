import 'package:flutter/services.dart';
import 'package:material_ui/material_ui.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'core/app_controller.dart';
import 'core/preferences.dart';
import 'core/speech.dart';
import 'ui/auth_pages.dart';
import 'ui/common.dart';
import 'ui/home_shell.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  SystemChrome.setSystemUIOverlayStyle(
    const SystemUiOverlayStyle(
      statusBarColor: Colors.transparent,
      statusBarIconBrightness: Brightness.dark,
      systemNavigationBarColor: pageBackground,
      systemNavigationBarIconBrightness: Brightness.dark,
    ),
  );
  final preferences = await SharedPreferences.getInstance();
  final controller = AppController(AppPreferences(preferences), SpeechOutput());
  runApp(QimingTablet(controller: controller));
  controller.initialize();
}

class QimingTablet extends StatelessWidget {
  const QimingTablet({super.key, required this.controller});
  final AppController controller;
  @override
  Widget build(BuildContext context) => MaterialApp(
    title: '启明帽 · 平板端',
    debugShowCheckedModeBanner: false,
    themeMode: ThemeMode.light,
    theme: ThemeData(
      useMaterial3: true,
      brightness: Brightness.light,
      scaffoldBackgroundColor: pageBackground,
      colorScheme: ColorScheme.fromSeed(
        seedColor: accent,
        brightness: Brightness.light,
        primary: accent,
        onPrimary: Colors.white,
        secondary: aqua,
        onSecondary: ink,
        surface: surface,
        onSurface: ink,
        error: errorInk,
        onError: Colors.white,
      ),
      textTheme: ThemeData.light().textTheme.apply(
        bodyColor: ink,
        displayColor: ink,
      ),
      appBarTheme: const AppBarTheme(
        backgroundColor: pageBackground,
        foregroundColor: ink,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        scrolledUnderElevation: 0,
        systemOverlayStyle: SystemUiOverlayStyle(
          statusBarColor: Colors.transparent,
          statusBarIconBrightness: Brightness.dark,
          systemNavigationBarColor: surface,
          systemNavigationBarIconBrightness: Brightness.dark,
        ),
      ),
      iconTheme: const IconThemeData(color: muted),
      dividerTheme: const DividerThemeData(color: lineColor, thickness: 1),
      switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith(
          (states) =>
              states.contains(WidgetState.selected) ? Colors.white : muted,
        ),
        trackColor: WidgetStateProperty.resolveWith(
          (states) =>
              states.contains(WidgetState.selected) ? accent : idleSurface,
        ),
      ),
      sliderTheme: const SliderThemeData(
        activeTrackColor: accent,
        thumbColor: accent,
        inactiveTrackColor: lineColor,
        valueIndicatorColor: accent,
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: surface,
        surfaceTintColor: Colors.transparent,
        indicatorColor: accent,
        labelTextStyle: WidgetStateProperty.resolveWith(
          (states) => TextStyle(
            color: states.contains(WidgetState.selected) ? accent : muted,
            fontSize: 12,
            fontWeight: states.contains(WidgetState.selected)
                ? FontWeight.w600
                : FontWeight.w400,
          ),
        ),
        iconTheme: WidgetStateProperty.resolveWith(
          (states) => IconThemeData(
            color: states.contains(WidgetState.selected) ? Colors.white : muted,
          ),
        ),
      ),
      navigationRailTheme: const NavigationRailThemeData(
        backgroundColor: pageBackground,
        indicatorColor: accent,
        selectedIconTheme: IconThemeData(color: Colors.white),
        unselectedIconTheme: IconThemeData(color: muted),
        selectedLabelTextStyle: TextStyle(
          color: accent,
          fontWeight: FontWeight.w600,
        ),
        unselectedLabelTextStyle: TextStyle(color: muted),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: const Color(0xfff8fafc),
        labelStyle: const TextStyle(color: bodyText),
        hintStyle: const TextStyle(color: muted),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide: const BorderSide(color: lineColor),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide: const BorderSide(color: lineColor),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide: const BorderSide(color: accent, width: 1.5),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide: const BorderSide(color: errorInk),
        ),
        focusedErrorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide: const BorderSide(color: errorInk, width: 1.5),
        ),
        contentPadding: const EdgeInsets.symmetric(
          horizontal: 16,
          vertical: 16,
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size(64, 48),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          minimumSize: const Size(64, 48),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
        ),
      ),
    ),
    home: AnimatedBuilder(
      animation: controller,
      builder: (context, _) {
        if (controller.loading) {
          return const Scaffold(
            body: Center(
              child: CircularProgressIndicator(semanticsLabel: '正在恢复本地设置'),
            ),
          );
        }
        if (controller.api == null) return ConnectPage(app: controller);
        if (!controller.loggedIn) return LoginPage(app: controller);
        return HomeShell(app: controller);
      },
    ),
  );
}
