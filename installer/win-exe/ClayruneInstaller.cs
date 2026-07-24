// Clayrune Installer — thin Windows .exe bootstrap.
//
// This is a faithful port of installer/Clayrune-Setup.bat to a native .exe so
// users get a double-clickable installer instead of a .bat (which downloads
// with a scarier SmartScreen warning and reads as untrustworthy).
//
// It is INTENTIONALLY thin: it does no install work itself. It discloses what
// will happen, then hands off to the canonical PowerShell bootstrap
// (installer/install.ps1) fetched fresh from GitHub raw with a cache-bust, so
// the install logic always lives in ONE place and never goes stale inside a
// shipped binary.
//
// Built with the .NET Framework csc.exe that ships on every Windows 10/11 box
// (see build.ps1) — no build pipeline, no third-party tooling, no code-signing
// spend. Unsigned: SmartScreen will still show "More info -> Run anyway" once,
// same as the .bat, but it is now a normal app prompt rather than a
// downloaded-script prompt.
//
// Override the bootstrap URL for testing with the CLAYRUNE_PS1_URL env var.

using System;
using System.Diagnostics;
using System.Text;
using System.Threading;

internal static class ClayruneInstaller
{
    private const string DefaultPs1Url =
        "https://raw.githubusercontent.com/ronle/clayrune/master/installer/install.ps1";

    // Exit codes returned by installer/install.ps1. This is a CONTRACT — see
    // the "EXIT CODES" block at the top of that file. Keep them in sync.
    private const int RcOk           = 0;
    private const int RcPrereq       = 1;  // Node / Claude CLI / runtime shell
    private const int RcInstallStep  = 2;  // a [STEP n/5] failed
    private const int RcNotLoggedIn  = 3;  // Claude CLI present but not authed

    // Held for the process lifetime — see AcquireSingleInstance().
    private static Mutex _instanceLock;

    private static string Ps1Url()
    {
        var o = Environment.GetEnvironmentVariable("CLAYRUNE_PS1_URL");
        return string.IsNullOrWhiteSpace(o) ? DefaultPs1Url : o;
    }

    // One launch = one installer. A clean-VM smoke test (2026-07-23) saw a
    // single double-click produce TWO "Clayrune Installer" windows; the stray
    // second run raced the first one's `git clone`/`git pull` and left the
    // checkout diverged from origin, which then broke every later update.
    //
    // Note: there is no self-relaunch in this file, so the duplicate came from
    // outside it (Explorer double-activation / SmartScreen re-launch / a second
    // human double-click). A named mutex makes the symptom impossible whatever
    // the cause, and — more importantly — makes two concurrent installs writing
    // the same directory impossible.
    //
    // The loser exits IMMEDIATELY with no pause, so a spurious second window
    // closes on its own and the user is left looking at exactly one. The line
    // it prints is still visible when the exe is run from an existing console.
    // Never let a mutex problem block a real install: on any failure, proceed.
    private static bool AcquireSingleInstance()
    {
        try
        {
            bool createdNew;
            _instanceLock = new Mutex(true, @"Local\ClayruneInstaller.SingleInstance", out createdNew);
            return createdNew;
        }
        catch
        {
            return true;
        }
    }

    private static int Main()
    {
        try { Console.OutputEncoding = Encoding.UTF8; } catch { /* legacy console */ }
        Console.Title = "Clayrune Installer";

        if (!AcquireSingleInstance())
        {
            Console.WriteLine("Clayrune Installer is already running in another window.");
            return RcOk;
        }

        Console.WriteLine();
        Console.WriteLine("============================================================");
        Console.WriteLine("  Clayrune Installer");
        Console.WriteLine("============================================================");
        Console.WriteLine();
        Console.WriteLine("This will install Clayrune on this computer.");
        Console.WriteLine();
        Console.WriteLine("It will:");
        Console.WriteLine("  1. Install Node.js LTS (if missing)");
        Console.WriteLine("  2. Install Git for Windows (needed by Claude Code)");
        Console.WriteLine("  3. Install Claude CLI");
        Console.WriteLine("  4. Ask you to log in once (browser opens for OAuth)");
        Console.WriteLine("  5. Clone Clayrune to %USERPROFILE%\\Clayrune");
        Console.WriteLine("  6. Set up Python dependencies + a Desktop shortcut");
        Console.WriteLine("  7. Open the dashboard in your browser");
        Console.WriteLine();
        Console.WriteLine("Estimated time: 5-10 minutes.");
        Console.WriteLine("Disk space: about 500 MB.");
        Console.WriteLine();
        Console.WriteLine("You can audit what runs by reading:");
        Console.WriteLine("  https://raw.githubusercontent.com/ronle/clayrune/master/installer/install-prompt.md");
        Console.WriteLine();
        Pause("Press Enter to begin (or close this window to cancel) . . .");

        while (true)
        {
            Console.WriteLine();
            Console.WriteLine("Starting installer...");
            Console.WriteLine();

            int rc = RunBootstrap();

            Console.WriteLine();
            Console.WriteLine("============================================================");
            if (rc == 0)
            {
                Console.WriteLine("  Done.");
                Console.WriteLine();
                Console.WriteLine("  You'll find a \"Clayrune\" shortcut on your Desktop and in");
                Console.WriteLine("  your Start Menu. Double-click it any time to launch.");
                Console.WriteLine("============================================================");
                Console.WriteLine();
                Pause("Press Enter to close this window . . .");
                return 0;
            }

            Console.WriteLine("  Installer paused.");
            Console.WriteLine();

            // Diagnose the ACTUAL failure. Previously every non-zero exit was
            // reported as "you probably aren't logged in", which sent a user
            // through a pointless OAuth login while the real failure was git
            // (clean-VM smoke test, 2026-07-23) — they logged in and hit the
            // identical error. Offer [L] only when login is plausibly the fix.
            bool offerLogin = (rc == RcNotLoggedIn || rc == RcPrereq);
            switch (rc)
            {
                case RcNotLoggedIn:
                    Console.WriteLine("  Claude CLI is installed but not logged in yet.");
                    Console.WriteLine("  We can handle the login for you - pick L below.");
                    break;
                case RcInstallStep:
                    Console.WriteLine("  An install step failed. Scroll up to the red line that");
                    Console.WriteLine("  starts with [STEP n/5] FAIL - it names the exact step and");
                    Console.WriteLine("  what to do about it.");
                    Console.WriteLine();
                    Console.WriteLine("  This is NOT a login problem, so logging in will not help.");
                    Console.WriteLine("  Common causes: no internet, git/Python missing or blocked");
                    Console.WriteLine("  by antivirus, or a damaged checkout in %USERPROFILE%\\Clayrune.");
                    break;
                case RcPrereq:
                    Console.WriteLine("  A prerequisite could not be installed (Node.js, Git, or the");
                    Console.WriteLine("  Claude CLI). The output above says which one and how to");
                    Console.WriteLine("  install it by hand.");
                    Console.WriteLine();
                    Console.WriteLine("  If the output above says \"not authenticated\", pick L.");
                    Console.WriteLine("  Otherwise install the missing piece first, then pick R.");
                    break;
                default:
                    Console.WriteLine("  The installer stopped with an unexpected error (exit code "
                                      + rc + ").");
                    Console.WriteLine("  The full output above shows what happened.");
                    offerLogin = true;
                    break;
            }
            Console.WriteLine("============================================================");
            Console.WriteLine();
            Console.WriteLine("  What now?");
            if (offerLogin)
                Console.WriteLine("    [L] Log me in to Claude now (opens browser, then re-runs installer)");
            Console.WriteLine("    [R] Retry the installer (if you've already fixed the issue)");
            Console.WriteLine("    [Q] Quit and close this window");
            Console.WriteLine();

            string allowed = offerLogin ? "LRQ" : "RQ";
            string prompt = offerLogin
                ? "Press L, R, or Q then Enter: "
                : "Press R or Q then Enter: ";
            char choice = ReadChoice(prompt, allowed);
            if (choice == 'Q') return rc;
            if (choice == 'L') DoLogin();
            // L and R both fall through to the top of the loop (re-run).
        }
    }

    // Hand off to the canonical PowerShell bootstrap, fetched fresh with a
    // cache-bust query param (GitHub raw is CDN-cached and can serve a stale
    // copy for minutes after a push — critical when shipping a hotfix while a
    // broken install.ps1 is still live on a fresh VM).
    private static int RunBootstrap()
    {
        long cb = DateTimeOffset.Now.ToUnixTimeSeconds();
        string url = Ps1Url() + (Ps1Url().Contains("?") ? "&" : "?") + "t=" + cb;
        string ps =
            "$ProgressPreference='SilentlyContinue'; " +
            "iwr \"" + url + "\" -useb | iex";

        var psi = new ProcessStartInfo
        {
            FileName = "powershell.exe",
            Arguments = "-ExecutionPolicy Bypass -NoProfile -Command \"" + ps.Replace("\"", "\\\"") + "\"",
            UseShellExecute = false,
        };
        try
        {
            using (var p = Process.Start(psi))
            {
                p.WaitForExit();
                return p.ExitCode;
            }
        }
        catch (Exception e)
        {
            Console.WriteLine();
            Console.WriteLine("Could not launch PowerShell: " + e.Message);
            return 1;
        }
    }

    // Spawn `claude /login` in a SEPARATE window and block until it closes.
    // PATH is rebuilt from the registry because install.ps1 just added
    // %APPDATA%\npm (where Claude CLI lives) to the USER PATH, but THIS
    // process inherited its PATH at launch — before that change — so a child
    // would not find `claude`. PowerShell can rebuild $env:Path per call.
    private static void DoLogin()
    {
        Console.WriteLine();
        Console.WriteLine("============================================================");
        Console.WriteLine("  Launching Claude login in a new window");
        Console.WriteLine("============================================================");
        Console.WriteLine();
        Console.WriteLine("A second window will open running `claude /login`.");
        Console.WriteLine("  1. A browser opens. Sign in with your Anthropic account");
        Console.WriteLine("     (Claude Pro/Max OAuth), or paste an API key when prompted.");
        Console.WriteLine("  2. When you see \"Logged in successfully\", type:  exit");
        Console.WriteLine("  3. The login window closes on its own.");
        Console.WriteLine();
        Console.WriteLine("This window keeps running and picks up where you left off.");
        Console.WriteLine();
        Pause("Press Enter to open the login window . . .");

        string inner =
            "$env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' + " +
            "[System.Environment]::GetEnvironmentVariable('Path','User'); " +
            "$cl = Get-Command claude -ErrorAction SilentlyContinue; " +
            "if (-not $cl) { Write-Host ''; Write-Host 'ERROR: claude command not found.' " +
            "-ForegroundColor Red; Write-Host 'The installer should have installed it. " +
            "Close this window and pick [R] in the main window to retry.' } " +
            "else { Write-Host \"\"\"Found claude at: $($cl.Path)\"\"\" -ForegroundColor DarkGray; " +
            "Write-Host ''; & claude /login }; " +
            "Write-Host ''; Read-Host 'Press Enter to close this window'";

        // `cmd /c start "title" /WAIT powershell ...` gives the login its own
        // console window and blocks us until it closes.
        var psi = new ProcessStartInfo
        {
            FileName = "cmd.exe",
            Arguments = "/c start \"Clayrune - Claude Login\" /WAIT powershell.exe " +
                        "-NoProfile -ExecutionPolicy Bypass -Command \"" +
                        inner.Replace("\"", "\\\"") + "\"",
            UseShellExecute = false,
        };
        try
        {
            using (var p = Process.Start(psi)) { p.WaitForExit(); }
        }
        catch (Exception e)
        {
            Console.WriteLine("Could not launch the login window: " + e.Message);
        }

        Console.WriteLine();
        Console.WriteLine("============================================================");
        Console.WriteLine("Login window closed. Retrying the installer...");
        Console.WriteLine("============================================================");
    }

    private static void Pause(string prompt)
    {
        Console.Write(prompt);
        try { Console.ReadLine(); } catch { /* no stdin (rare) */ }
    }

    private static char ReadChoice(string prompt, string allowed)
    {
        while (true)
        {
            Console.Write(prompt);
            string line = Console.ReadLine();
            // null == stdin at EOF (closed/redirected). Don't spin forever:
            // the safe interpretation of "no input" here is quit.
            if (line == null) return 'Q';
            if (line.Trim().Length > 0)
            {
                char c = char.ToUpperInvariant(line.Trim()[0]);
                if (allowed.IndexOf(c) >= 0) return c;
            }
            Console.WriteLine("Please enter one of: " + string.Join(", ", allowed.ToCharArray()));
        }
    }
}
