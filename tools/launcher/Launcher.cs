// aplikace.exe — double-click and Prizma is running.
//
// WHY C#, AND WHY THIS SHAPE
//
// PyInstaller is not an option on this machine: pip hits a RecursionError in
// setuptools here, and an executable that cannot be rebuilt is worse than none.
// csc.exe ships with Windows itself (C:\Windows\Microsoft.NET\Framework64\
// v4.0.30319\csc.exe), so this compiles on a clean install with nothing
// downloaded and produces one ~15 KB file with no runtime to install.
//
// It starts ONE process, not two. `backend/app/spa.py` mounts `frontend/dist`
// into the same FastAPI app, so the built frontend and the API share an origin
// and a port. Starting Vite alongside it would add a second thing to keep
// alive, a second port, and a CORS story — for a build that is already on disk.
// The frontend is "started" in the sense that matters: it is served, and the
// browser opens on it.
//
// C# 5 only. The compiler in v4.0.30319 predates string interpolation,
// expression-bodied members and `nameof`, so this is string.Format throughout.
// It is not old-fashioned by accident.

using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

internal static class Launcher
{
    private const int FirstPort = 8000;
    private const int LastPort = 8010;
    private const int StartupTimeoutSeconds = 90;

    private static int Main()
    {
        try
        {
            Console.OutputEncoding = Encoding.UTF8;
        }
        catch (Exception)
        {
            // A console that cannot do UTF-8 still runs the app; only the
            // diacritics suffer. Never fail the launch over an encoding.
        }

        Console.Title = "Prizma";
        Say("Prizma");
        Say("");

        try
        {
            return Run();
        }
        catch (Exception error)
        {
            Fail("Neočekávaná chyba: " + error.Message);
            return 1;
        }
    }

    private static int Run()
    {
        string root = FindRoot();
        if (root == null)
        {
            Fail("Nenašel jsem složku projektu.");
            Say("Tenhle soubor musí ležet v kořeni projektu — tam, kde jsou složky");
            Say("backend a frontend. Zkopíruj ho zpátky a spusť znovu.");
            return Wait(1);
        }

        string python = Path.Combine(root, @"backend\.venv\Scripts\python.exe");
        if (!File.Exists(python))
        {
            Fail("Chybí prostředí Pythonu: backend\\.venv");
            Say("Vytvoř ho jednou příkazem, pak už bude .exe fungovat samo:");
            Say("");
            Say("    cd backend");
            Say("    python -m venv .venv");
            Say("    .venv\\Scripts\\pip install -e .");
            return Wait(1);
        }

        string index = Path.Combine(root, @"frontend\dist\index.html");
        if (!File.Exists(index) && !TryBuildFrontend(root))
        {
            Fail("Frontend není sestavený a nepodařilo se ho sestavit.");
            Say("Sestav ho ručně a spusť .exe znovu:");
            Say("");
            Say("    cd frontend");
            Say("    npm install");
            Say("    npm run build");
            return Wait(1);
        }

        // Someone already has it open. Opening a second server on another port
        // would give them a second, empty database view of the same app —
        // pointing the browser at the running one is what they meant.
        int running = FindRunningInstance();
        if (running != 0)
        {
            Say("Prizma už běží. Otevírám ji.");
            OpenBrowser(running);
            return 0;
        }

        int port = FirstFreePort();
        if (port == 0)
        {
            Fail(string.Format("Porty {0}–{1} jsou obsazené.", FirstPort, LastPort));
            return Wait(1);
        }

        Say("Spouštím server…");
        Process server = StartServer(root, python, port);
        if (server == null)
        {
            Fail("Server se nepodařilo spustit.");
            return Wait(1);
        }

        // The job object is what makes closing this window stop the server.
        // Without it the X button orphans uvicorn, the port stays taken, and
        // the next launch quietly lands on a stale process holding an old
        // build — the worst kind of bug, because everything looks fine.
        KillChildWithMe(server);

        if (!WaitForServer(port, server))
        {
            Fail("Server nenaběhl.");
            Say("Podrobnosti jsou ve výpisu výše.");
            StopServer(server);
            ForgetInstance();
            return Wait(1);
        }

        RememberInstance(port);

        Say("");
        Say(string.Format("Běží na http://127.0.0.1:{0}", port));
        Say("Otevírám prohlížeč. Tohle okno nechej otevřené — zavřením se aplikace vypne.");
        Say("");
        OpenBrowser(port);

        server.WaitForExit();
        ForgetInstance();
        Say("");
        Say("Server skončil.");
        return 0;
    }

    // ------------------------------------------------------------ locating

    /// <summary>The project root: the first folder above us holding the app.</summary>
    private static string FindRoot()
    {
        // Walking up rather than trusting the exe's own folder, so the file
        // still works from a Desktop shortcut or a tools\ subfolder.
        DirectoryInfo dir = new DirectoryInfo(AppDomain.CurrentDomain.BaseDirectory);
        while (dir != null)
        {
            if (File.Exists(Path.Combine(dir.FullName, @"backend\app\main.py")))
            {
                return dir.FullName;
            }
            dir = dir.Parent;
        }
        return null;
    }

    // ------------------------------------------------------------ frontend

    /// <summary>Builds `frontend/dist` when it is missing. Needs Node.</summary>
    private static bool TryBuildFrontend(string root)
    {
        Say("Frontend ještě není sestavený, sestavuji ho (jednorázově, chvíli to trvá)…");

        string frontend = Path.Combine(root, "frontend");
        if (!Directory.Exists(Path.Combine(frontend, "node_modules")) && !RunNpm(frontend, "install"))
        {
            return false;
        }
        if (!RunNpm(frontend, "run build"))
        {
            return false;
        }
        return File.Exists(Path.Combine(frontend, @"dist\index.html"));
    }

    /// <summary>npm is a .cmd, so it is run through the shell interpreter.</summary>
    private static bool RunNpm(string workingDirectory, string arguments)
    {
        try
        {
            ProcessStartInfo info = new ProcessStartInfo("cmd.exe", "/c npm " + arguments);
            info.WorkingDirectory = workingDirectory;
            info.UseShellExecute = false;
            using (Process npm = Process.Start(info))
            {
                npm.WaitForExit();
                return npm.ExitCode == 0;
            }
        }
        catch (Exception error)
        {
            Say("npm se nepodařilo spustit: " + error.Message);
            return false;
        }
    }

    // -------------------------------------------------------------- server

    private static Process StartServer(string root, string python, int port)
    {
        ProcessStartInfo info = new ProcessStartInfo(python);
        info.Arguments = string.Format(
            "-m uvicorn app.main:app --host 127.0.0.1 --port {0}", port);
        info.WorkingDirectory = Path.Combine(root, "backend");
        // Inherited console: uvicorn's own log is the only diagnosis anyone
        // gets when a provider key or a migration is wrong, and hiding it to
        // look tidy would mean answering "it doesn't work" with a shrug.
        info.UseShellExecute = false;

        try
        {
            return Process.Start(info);
        }
        catch (Exception error)
        {
            Say(error.Message);
            return null;
        }
    }

    private static void StopServer(Process server)
    {
        try
        {
            if (!server.HasExited)
            {
                server.Kill();
            }
        }
        catch (Exception)
        {
            // Already gone, or gone between the check and the kill. Either way
            // there is nothing left to stop.
        }
    }

    /// <summary>Waits until uvicorn is accepting connections, or it dies.</summary>
    private static bool WaitForServer(int port, Process server)
    {
        DateTime deadline = DateTime.UtcNow.AddSeconds(StartupTimeoutSeconds);
        while (DateTime.UtcNow < deadline)
        {
            if (server.HasExited)
            {
                return false;
            }
            if (IsListening(port))
            {
                return true;
            }
            Thread.Sleep(300);
        }
        return false;
    }

    /// <summary>
    /// Whether something accepts a connection on this port.
    ///
    /// A TCP connect, deliberately, and not a GET /api/health. Asking over
    /// HTTP is the obvious readiness check and it does not work on this
    /// machine: Norton lets the connection open and then never delivers the
    /// response to an unsigned .NET binary, so the probe burns its whole
    /// timeout against a server that curl and the browser talk to happily —
    /// twenty seconds was not enough. A connect is a kernel call the security
    /// suite is content to allow, and it answers the only question worth
    /// asking here: the port was picked because it was free, so whatever is
    /// listening on it is the uvicorn that was just started.
    ///
    /// Connecting rather than binding matters too. A bind test would briefly
    /// hold the port uvicorn is at that moment trying to take, and losing that
    /// race would kill the server with "address already in use".
    /// </summary>
    private static bool IsListening(int port)
    {
        try
        {
            using (System.Net.Sockets.TcpClient client = new System.Net.Sockets.TcpClient())
            {
                IAsyncResult attempt = client.BeginConnect(IPAddress.Loopback, port, null, null);
                // A closed loopback port normally refuses at once. Under a
                // firewall that drops instead of refusing, it never answers at
                // all — hence a short explicit wait rather than the default,
                // which would stall the poll on a port nobody is on.
                if (!attempt.AsyncWaitHandle.WaitOne(400))
                {
                    return false;
                }
                client.EndConnect(attempt);
                return client.Connected;
            }
        }
        catch (Exception)
        {
            return false;
        }
    }

    /// <summary>Where a running instance leaves its port number.</summary>
    private static string InstanceFile()
    {
        string dir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Prizma");
        return Path.Combine(dir, "instance.port");
    }

    private static void RememberInstance(int port)
    {
        try
        {
            string file = InstanceFile();
            Directory.CreateDirectory(Path.GetDirectoryName(file));
            File.WriteAllText(file, port.ToString());
        }
        catch (Exception)
        {
            // Costs a duplicate window on the next launch and nothing else.
            // Never worth failing a launch that otherwise succeeded.
        }
    }

    private static void ForgetInstance()
    {
        try
        {
            File.Delete(InstanceFile());
        }
        catch (Exception)
        {
            // Someone else's problem by now; the next launch rechecks the port.
        }
    }

    /// <summary>Port of an instance already serving Prizma, or 0.</summary>
    private static int FindRunningInstance()
    {
        // Read the port a previous launch wrote down, rather than scanning the
        // whole range and asking each port whether it is Prizma. The scan cost
        // twenty seconds of a black window here — every closed port is dropped
        // rather than refused, so each probe ran to its timeout — and the
        // question could not be answered anyway, because HTTP from this binary
        // to loopback never comes back (see IsListening).
        //
        // The file is weaker evidence than an answer from /api/health: it says
        // "a server was started on this port", not "that server is still the
        // one there". A live connect closes most of the gap. What is left — a
        // stale note whose port some other app has since taken — opens a
        // browser tab on the wrong thing, which is visible and closable. A
        // second copy of the app on a second port is not.
        try
        {
            string file = InstanceFile();
            if (!File.Exists(file))
            {
                return 0;
            }

            int port;
            if (!int.TryParse(File.ReadAllText(file).Trim(), out port)
                || port < FirstPort || port > LastPort)
            {
                ForgetInstance();
                return 0;
            }

            if (IsListening(port))
            {
                return port;
            }

            // Nothing there any more: the last run was killed hard enough that
            // it never cleaned up. Drop the note and start fresh.
            ForgetInstance();
            return 0;
        }
        catch (Exception)
        {
            return 0;
        }
    }

    private static int FirstFreePort()
    {
        for (int port = FirstPort; port <= LastPort; port++)
        {
            if (IsPortFree(port))
            {
                return port;
            }
        }
        return 0;
    }

    private static bool IsPortFree(int port)
    {
        System.Net.Sockets.TcpListener listener = null;
        try
        {
            listener = new System.Net.Sockets.TcpListener(IPAddress.Loopback, port);
            listener.Start();
            return true;
        }
        catch (Exception)
        {
            return false;
        }
        finally
        {
            if (listener != null)
            {
                listener.Stop();
            }
        }
    }

    private static void OpenBrowser(int port)
    {
        try
        {
            ProcessStartInfo info = new ProcessStartInfo(
                string.Format("http://127.0.0.1:{0}", port));
            info.UseShellExecute = true;
            Process.Start(info);
        }
        catch (Exception)
        {
            Say(string.Format("Prohlížeč se neotevřel — zadej http://127.0.0.1:{0} ručně.", port));
        }
    }

    // ----------------------------------------------------------- job object

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr CreateJobObject(IntPtr attributes, string name);

    [DllImport("kernel32.dll")]
    private static extern bool SetInformationJobObject(
        IntPtr job, int infoClass, IntPtr info, uint length);

    [DllImport("kernel32.dll")]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    private const int ExtendedLimitInformation = 9;
    private const uint KillOnJobClose = 0x2000;

    [StructLayout(LayoutKind.Sequential)]
    private struct BasicLimitInformation
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters
    {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ExtendedLimitInfo
    {
        public BasicLimitInformation BasicLimitInformation;
        public IoCounters IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    /// <summary>
    /// Ties the server's lifetime to this window's.
    ///
    /// A ProcessExit handler is not enough: closing a console with the X button
    /// or ending the task does not run one. A job object with
    /// JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE is enforced by the kernel, so the
    /// server dies with the launcher however the launcher dies.
    /// </summary>
    private static void KillChildWithMe(Process child)
    {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero)
        {
            return;
        }

        ExtendedLimitInfo limits = new ExtendedLimitInfo();
        limits.BasicLimitInformation.LimitFlags = KillOnJobClose;

        int size = Marshal.SizeOf(typeof(ExtendedLimitInfo));
        IntPtr buffer = Marshal.AllocHGlobal(size);
        try
        {
            Marshal.StructureToPtr(limits, buffer, false);
            if (SetInformationJobObject(job, ExtendedLimitInformation, buffer, (uint)size))
            {
                AssignProcessToJobObject(job, child.Handle);
            }
        }
        catch (Exception)
        {
            // Worst case the server outlives the window and the next launch
            // finds it through FindRunningInstance. Not a reason to refuse to
            // start.
        }
        finally
        {
            Marshal.FreeHGlobal(buffer);
        }
    }

    // ------------------------------------------------------------- console

    private static void Say(string line)
    {
        Console.WriteLine(line);
    }

    private static void Fail(string line)
    {
        Console.WriteLine();
        Console.WriteLine(line);
    }

    /// <summary>Keeps the window open so the message above can be read.</summary>
    private static int Wait(int exitCode)
    {
        Console.WriteLine();
        Console.WriteLine("Stiskni Enter pro zavření.");
        try
        {
            Console.ReadLine();
        }
        catch (Exception)
        {
            // No console to read from (started without one). Exit quietly.
        }
        return exitCode;
    }
}
