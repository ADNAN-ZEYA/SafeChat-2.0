import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:flutter_animate/flutter_animate.dart';
import '../../../theme/theme_provider.dart';
import 'auth_provider.dart';

class LoginScreen extends ConsumerStatefulWidget {
  const LoginScreen({super.key});

  @override
  ConsumerState<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends ConsumerState<LoginScreen> {
  final _emailController = TextEditingController();
  final _passwordController = TextEditingController();
  final _formKey = GlobalKey<FormState>();
  bool _obscurePassword = true;

  @override
  void dispose() {
    _emailController.dispose();
    _passwordController.dispose();
    super.dispose();
  }

  /// Sends a Firebase password-reset email to the address in the email field
  /// (CQ-09). Response is intentionally the same whether or not the address is
  /// registered, to avoid account enumeration.
  Future<void> _handleForgotPassword() async {
    final email = _emailController.text.trim();
    final messenger = ScaffoldMessenger.of(context);
    if (email.isEmpty) {
      messenger.showSnackBar(
        const SnackBar(
          content: Text('Enter your email above, then tap "Forgot Password?".'),
        ),
      );
      return;
    }
    try {
      await ref.read(authRepositoryProvider).sendPasswordResetEmail(email);
      messenger.showSnackBar(
        const SnackBar(
          content: Text(
            'If an account exists for that email, a reset link has been sent.',
          ),
        ),
      );
    } catch (e) {
      messenger.showSnackBar(
        SnackBar(content: Text(e.toString().replaceFirst('Exception: ', ''))),
      );
    }
  }

  Future<void> _handleLogin() async {
    if (_formKey.currentState?.validate() ?? false) {
      final email = _emailController.text.trim();
      final password = _passwordController.text;

      await ref
          .read(authControllerProvider.notifier)
          .signInWithEmailAndPassword(email: email, password: password);

      if (mounted) {
        final authState = ref.read(authControllerProvider);
        if (authState.error != null) {
          ScaffoldMessenger.of(
            context,
          ).showSnackBar(SnackBar(content: Text(authState.error!)));
        } else if (authState.isAuthenticated) {
          if (authState.needsOnboarding) {
            context.go('/onboarding');
          } else {
            context.go('/home');
          }
        }
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final brightness = ref.watch(brightnessProvider);
    final isDark =
        brightness == ThemeMode.dark ||
        (brightness == ThemeMode.system &&
            MediaQuery.of(context).platformBrightness == Brightness.dark);

    return Scaffold(
      appBar: AppBar(
        title: const Text('SafeChat'),
        actions: [
          IconButton(
            icon: Icon(isDark ? Icons.light_mode : Icons.dark_mode),
            tooltip: 'Toggle Light/Dark',
            onPressed: () {
              ref.read(brightnessProvider.notifier).toggleBrightness();
            },
          ),
        ],
      ),
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.symmetric(
              horizontal: 24.0,
              vertical: 16.0,
            ),
            child: Form(
              key: _formKey,
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children:
                    [
                          const Icon(
                            Icons.chat_bubble_rounded,
                            size: 64,
                            color: Colors.blueAccent,
                          ),
                          const SizedBox(height: 24),
                          Text(
                            'Welcome Back',
                            textAlign: TextAlign.center,
                            style: Theme.of(context).textTheme.headlineMedium
                                ?.copyWith(fontWeight: FontWeight.bold),
                          ),
                          const SizedBox(height: 8),
                          Text(
                            'Login to continue to SafeChat',
                            textAlign: TextAlign.center,
                            style: Theme.of(context).textTheme.bodyLarge
                                ?.copyWith(
                                  color: Theme.of(context).colorScheme.onSurface
                                      .withValues(alpha: 0.7),
                                ),
                          ),
                          const SizedBox(height: 48),
                          TextFormField(
                            controller: _emailController,
                            keyboardType: TextInputType.emailAddress,
                            decoration: const InputDecoration(
                              labelText: 'Email',
                              prefixIcon: Icon(Icons.email_outlined),
                              hintText: 'Enter your email',
                            ),
                            validator: (value) {
                              if (value == null || value.isEmpty) {
                                return 'Please enter your email';
                              }
                              return null;
                            },
                          ),
                          const SizedBox(height: 16),
                          TextFormField(
                            controller: _passwordController,
                            obscureText: _obscurePassword,
                            decoration: InputDecoration(
                              labelText: 'Password',
                              prefixIcon: const Icon(Icons.lock_outline),
                              hintText: 'Enter your password',
                              suffixIcon: IconButton(
                                icon: Icon(
                                  _obscurePassword
                                      ? Icons.visibility_off
                                      : Icons.visibility,
                                ),
                                onPressed: () {
                                  setState(() {
                                    _obscurePassword = !_obscurePassword;
                                  });
                                },
                              ),
                            ),
                            validator: (value) {
                              if (value == null || value.isEmpty) {
                                return 'Please enter your password';
                              }
                              return null;
                            },
                          ),
                          const SizedBox(height: 12),
                          Align(
                            alignment: Alignment.centerRight,
                            child: TextButton(
                              onPressed: _handleForgotPassword,
                              child: const Text('Forgot Password?'),
                            ),
                          ),
                          const SizedBox(height: 24),
                          Consumer(
                            builder: (context, ref, child) {
                              final authState = ref.watch(
                                authControllerProvider,
                              );
                              final isLoading = authState.isLoading;
                              return ElevatedButton(
                                onPressed: isLoading ? null : _handleLogin,
                                style: ElevatedButton.styleFrom(
                                  padding: const EdgeInsets.symmetric(
                                    vertical: 16,
                                  ),
                                ),
                                child: isLoading
                                    ? const SizedBox(
                                        width: 24,
                                        height: 24,
                                        child: CircularProgressIndicator(
                                          strokeWidth: 2,
                                        ),
                                      )
                                    : const Text(
                                        'Sign In',
                                        style: TextStyle(
                                          fontSize: 16,
                                          fontWeight: FontWeight.bold,
                                        ),
                                      ),
                              );
                            },
                          ),
                          const SizedBox(height: 24),
                          Row(
                            children: [
                              Expanded(
                                child: Divider(
                                  color: Theme.of(context).colorScheme.onSurface
                                      .withValues(alpha: 0.2),
                                ),
                              ),
                              Padding(
                                padding: const EdgeInsets.symmetric(
                                  horizontal: 16,
                                ),
                                child: Text(
                                  'OR',
                                  style: TextStyle(
                                    color: Theme.of(context)
                                        .colorScheme
                                        .onSurface
                                        .withValues(alpha: 0.5),
                                    fontWeight: FontWeight.bold,
                                  ),
                                ),
                              ),
                              Expanded(
                                child: Divider(
                                  color: Theme.of(context).colorScheme.onSurface
                                      .withValues(alpha: 0.2),
                                ),
                              ),
                            ],
                          ),
                          const SizedBox(height: 24),
                          Consumer(
                            builder: (context, ref, child) {
                              final authState = ref.watch(
                                authControllerProvider,
                              );
                              final isLoading = authState.isLoading;
                              return OutlinedButton.icon(
                                onPressed: isLoading
                                    ? null
                                    : () async {
                                        await ref
                                            .read(
                                              authControllerProvider.notifier,
                                            )
                                            .signInWithGoogle();
                                        if (context.mounted) {
                                          final newAuthState = ref.read(
                                            authControllerProvider,
                                          );
                                          if (newAuthState.error != null) {
                                            ScaffoldMessenger.of(
                                              context,
                                            ).showSnackBar(
                                              SnackBar(
                                                content: Text(authState.error!),
                                              ),
                                            );
                                          } else if (newAuthState
                                              .isAuthenticated) {
                                            if (newAuthState.needsOnboarding) {
                                              context.go('/onboarding');
                                            } else {
                                              context.go('/home');
                                            }
                                          }
                                        }
                                      },
                                icon: isLoading
                                    ? const SizedBox(
                                        width: 20,
                                        height: 20,
                                        child: CircularProgressIndicator(
                                          strokeWidth: 2,
                                        ),
                                      )
                                    : const Text(
                                        'G',
                                        style: TextStyle(
                                          fontSize: 20,
                                          fontWeight: FontWeight.bold,
                                          color: Colors.blue,
                                        ),
                                      ),
                                label: Text(
                                  isLoading
                                      ? 'Signing in...'
                                      : 'Sign in with Google',
                                  style: const TextStyle(
                                    fontSize: 16,
                                    fontWeight: FontWeight.bold,
                                  ),
                                ),
                              );
                            },
                          ),
                          const SizedBox(height: 24),
                          Row(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              Text(
                                "Don't have an account?",
                                style: TextStyle(
                                  color: Theme.of(context).colorScheme.onSurface
                                      .withValues(alpha: 0.7),
                                ),
                              ),
                              TextButton(
                                onPressed: () {
                                  context.push('/signup');
                                },
                                child: const Text(
                                  'Sign Up',
                                  style: TextStyle(fontWeight: FontWeight.bold),
                                ),
                              ),
                            ],
                          ),
                        ]
                        .animate(interval: 50.ms)
                        .fade(duration: 400.ms)
                        .slideY(begin: 0.1, end: 0, curve: Curves.easeOut),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
