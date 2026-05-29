using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;

namespace MultiTradingCustomerLauncher;

internal static class Program
{
    private const int ApiPort = 8010;
    private const int WebPort = 3010;

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int MessageBox(IntPtr hWnd, string text, string caption, uint type);

    [STAThread]
    private static int Main()
    {
        using var mutex = new Mutex(initiallyOwned: true, name: @"Global\MultiTradingCustomerLauncher_SingleInstance_v1", out var created);
        if (!created)
        {
            Show("MultiTrading 已在启动中或已运行。请稍候查看浏览器页面。");
            return 0;
        }

        var root = AppContext.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        var backend = Path.Combine(root, "Backend.exe");
        var node = Path.Combine(root, "runtime", "node", "node.exe");
        var frontendDir = Path.Combine(root, "frontend");
        var server = Path.Combine(frontendDir, "server.js");
        var backendLog = Path.Combine(root, "launcher_backend.log");
        var frontendLog = Path.Combine(root, "launcher_frontend.log");

        var missing = new[] { backend, node, server }.Where(p => !File.Exists(p)).ToArray();
        if (missing.Length > 0)
        {
            Show("安装目录不完整，缺少以下文件：\n\n" + string.Join("\n", missing), "MultiTrading 启动失败");
            return 1;
        }

        try
        {
            if (!HttpOk($"http://127.0.0.1:{ApiPort}/health", TimeSpan.FromSeconds(2)))
            {
                if (PortOpen(ApiPort))
                {
                    Show($"后端端口 {ApiPort} 已被占用，但健康检查未通过。请先关闭占用该端口的程序。", "MultiTrading 启动失败");
                    return 1;
                }
                StartLogged(backend, $"--host=127.0.0.1 --port={ApiPort}", root, backendLog, root);
            }

            if (!WaitHttp($"http://127.0.0.1:{ApiPort}/health", TimeSpan.FromSeconds(90)))
            {
                Show($"后端未能在限定时间内启动。请查看日志：\n{backendLog}", "MultiTrading 启动失败");
                return 1;
            }

            if (!HttpOk($"http://127.0.0.1:{WebPort}", TimeSpan.FromSeconds(2)))
            {
                if (PortOpen(WebPort))
                {
                    Show($"前端端口 {WebPort} 已被占用，但页面不可用。请先关闭占用该端口的程序。", "MultiTrading 启动失败");
                    return 1;
                }
                StartLogged(node, Quote(server), frontendDir, frontendLog, root);
            }

            if (!WaitHttp($"http://127.0.0.1:{WebPort}", TimeSpan.FromSeconds(120)))
            {
                Show($"前端未能在限定时间内启动。请查看日志：\n{frontendLog}", "MultiTrading 启动失败");
                return 1;
            }

            Process.Start(new ProcessStartInfo
            {
                FileName = $"http://127.0.0.1:{WebPort}/auth?forceLogin=1",
                UseShellExecute = true
            });
            return 0;
        }
        catch (Exception ex)
        {
            var crash = Path.Combine(root, "launcher_crash.log");
            TryAppend(crash, $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] {ex}\n");
            Show($"启动器异常：{ex.Message}\n\n日志：{crash}", "MultiTrading 启动失败");
            return 1;
        }
    }

    private static void StartLogged(string fileName, string arguments, string cwd, string logPath, string root)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(logPath)!);
        TryAppend(logPath, "\n" + new string('=', 72) + $"\n[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] cwd={cwd}\ncmd={fileName} {arguments}\n");

        var psi = new ProcessStartInfo
        {
            FileName = fileName,
            Arguments = arguments,
            WorkingDirectory = cwd,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        psi.Environment["MT_BUILD_TARGET"] = "customer";
        psi.Environment["NEXT_PUBLIC_MT_BUILD_TARGET"] = "customer";
        psi.Environment["MULTITRADING_ROOT"] = root;
        psi.Environment["LONGPORT_API_PORT"] = ApiPort.ToString();
        psi.Environment["LONGPORT_WEB_PORT"] = WebPort.ToString();
        psi.Environment["LOCAL_AGENT_ALLOW_USER_OWNERS"] = "true";
        psi.Environment["NEXT_TELEMETRY_DISABLED"] = "1";
        psi.Environment["PORT"] = WebPort.ToString();
        psi.Environment["HOSTNAME"] = "127.0.0.1";
        psi.Environment["NODE_ENV"] = "production";

        var process = new Process { StartInfo = psi, EnableRaisingEvents = true };
        process.OutputDataReceived += (_, e) => { if (e.Data != null) TryAppend(logPath, e.Data + Environment.NewLine); };
        process.ErrorDataReceived += (_, e) => { if (e.Data != null) TryAppend(logPath, e.Data + Environment.NewLine); };
        process.Start();
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
    }

    private static bool WaitHttp(string url, TimeSpan timeout)
    {
        var until = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < until)
        {
            if (HttpOk(url, TimeSpan.FromSeconds(3))) return true;
            Thread.Sleep(1000);
        }
        return HttpOk(url, TimeSpan.FromSeconds(3));
    }

    private static bool HttpOk(string url, TimeSpan timeout)
    {
        try
        {
            var req = WebRequest.CreateHttp(url);
            req.Timeout = (int)timeout.TotalMilliseconds;
            using var resp = (HttpWebResponse)req.GetResponse();
            return (int)resp.StatusCode is >= 200 and < 500;
        }
        catch
        {
            return false;
        }
    }

    private static bool PortOpen(int port)
    {
        try
        {
            using var tcp = new TcpClient();
            var task = tcp.ConnectAsync(IPAddress.Loopback, port);
            return task.Wait(TimeSpan.FromMilliseconds(450)) && tcp.Connected;
        }
        catch
        {
            return false;
        }
    }

    private static string Quote(string value) => "\"" + value.Replace("\"", "\\\"") + "\"";

    private static void TryAppend(string path, string text)
    {
        try { File.AppendAllText(path, text); } catch { /* ignore */ }
    }

    private static void Show(string text, string title = "MultiTrading")
    {
        if (OperatingSystem.IsWindows())
        {
            MessageBox(IntPtr.Zero, text, title, 0x40);
        }
        else
        {
            Console.WriteLine(text);
        }
    }
}
