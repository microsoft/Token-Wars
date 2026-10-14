using System.Globalization;
using System.Text;

namespace TokenWars;

/// <summary>Parsed command line (hand-rolled, no packages).</summary>
public sealed class CliOptions
{
    public string? Command { get; set; }
    public string? Question { get; set; }
    public int? Limit { get; set; }
    public bool Submit { get; set; }
    public bool NoJudge { get; set; }
    public bool Mock { get; set; }
    public bool Help { get; set; }
    public string Customer { get; set; } = "C1001";
    public string Models { get; set; } = "premium,balanced,economy,open";
    public string? StrategyPath { get; set; }

    private static readonly Dictionary<string, string[]> AllowedOptions = new(StringComparer.Ordinal)
    {
        ["run"] = new[] { "--limit", "--submit", "--no-judge" },
        ["ask"] = new[] { "--customer" },
        ["compare"] = new[] { "--models", "--no-judge", "--limit" },
        ["doctor"] = Array.Empty<string>(),
    };

    public static CliOptions Parse(string[] args)
    {
        var o = new CliOptions();
        var seen = new List<string>();
        for (int i = 0; i < args.Length; i++)
        {
            var arg = args[i];
            string? inlineValue = null;
            if (arg.StartsWith("--", StringComparison.Ordinal) && arg.Contains('='))
            {
                int eq = arg.IndexOf('=');
                inlineValue = arg.Substring(eq + 1);
                arg = arg.Substring(0, eq);
            }

            switch (arg)
            {
                case "-h":
                case "--help":
                    o.Help = true;
                    break;
                case "--mock":
                    o.Mock = true;
                    break;
                case "--strategy":
                    o.StrategyPath = inlineValue ?? NextValue(args, ref i, arg);
                    break;
                case "--submit":
                    o.Submit = true;
                    seen.Add(arg);
                    break;
                case "--no-judge":
                    o.NoJudge = true;
                    seen.Add(arg);
                    break;
                case "--limit":
                {
                    var value = inlineValue ?? NextValue(args, ref i, arg);
                    if (!int.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out var limit))
                        throw new ArgumentException($"argument --limit: invalid int value: '{value}'");
                    o.Limit = limit;
                    seen.Add(arg);
                    break;
                }
                case "--customer":
                    o.Customer = inlineValue ?? NextValue(args, ref i, arg);
                    seen.Add(arg);
                    break;
                case "--models":
                    o.Models = inlineValue ?? NextValue(args, ref i, arg);
                    seen.Add(arg);
                    break;
                default:
                    if (arg.StartsWith("-", StringComparison.Ordinal) && arg.Length > 1)
                        throw new ArgumentException($"unrecognized argument: {arg}");
                    if (o.Command == null) o.Command = arg;
                    else if (o.Command == "ask" && o.Question == null) o.Question = arg;
                    else throw new ArgumentException($"unrecognized argument: {arg}");
                    break;
            }
        }

        if (o.Help) return o;
        if (o.Command == null) throw new ArgumentException("a command is required: run | ask | compare | doctor");
        if (!AllowedOptions.TryGetValue(o.Command, out var allowed))
            throw new ArgumentException($"invalid command '{o.Command}' (choose from run, ask, compare, doctor)");
        foreach (var option in seen)
        {
            if (!allowed.Contains(option))
                throw new ArgumentException($"option {option} is not valid for '{o.Command}'");
        }
        if (o.Command == "ask" && string.IsNullOrWhiteSpace(o.Question))
            throw new ArgumentException("ask: the question is required, e.g. dotnet run -- ask \"Can I return shoes?\" --customer C1001");
        return o;
    }

    private static string NextValue(string[] args, ref int i, string name)
    {
        if (i + 1 >= args.Length) throw new ArgumentException($"argument {name}: expected one value");
        i++;
        return args[i];
    }
}

public static class Program
{
    private const string Usage =
        "Token Wars – ByteCart Support Copilot (.NET)\n" +
        "\n" +
        "Usage (run in dotnet/<variant>/TokenWars/):\n" +
        "  dotnet run -- run [--limit N] [--no-judge] [--submit]     answer the workload, judge it, print the scorecard\n" +
        "  dotnet run -- ask \"<question>\" [--customer C1001]         answer one question with the current strategy\n" +
        "  dotnet run -- compare [--models premium,balanced,economy,open] [--no-judge] [--limit N]\n" +
        "                                                            compare models on the 30 compare:true items\n" +
        "  dotnet run -- doctor                                      check configuration, data files and model connectivity\n" +
        "\n" +
        "Common options:\n" +
        "  --mock             offline mock mode (no Azure calls); same as TOKENWARS_MOCK=1\n" +
        "  --strategy PATH    path to strategy.json (default: ./strategy.json)\n";

    public static async Task<int> Main(string[] args)
    {
        try
        {
            Console.OutputEncoding = Encoding.UTF8;
        }
        catch (Exception)
        {
            // the console encoding cannot be changed on this terminal – ignore
        }

        CliOptions options;
        try
        {
            options = CliOptions.Parse(args);
        }
        catch (ArgumentException ex)
        {
            Console.Error.WriteLine(Usage);
            Console.Error.WriteLine($"error: {ex.Message}");
            return 2;
        }
        if (options.Help)
        {
            Console.WriteLine(Usage);
            return 0;
        }

        try
        {
            var cfg = AppConfig.Load(options.Mock, options.StrategyPath);
            var strategy = cfg.Strategy;
            switch (options.Command)
            {
                case "run":
                    return await Runner.RunAsync(cfg, strategy, options.Limit, !options.NoJudge, options.Submit);
                case "ask":
                    return await Runner.AskAsync(cfg, strategy, options.Question ?? "", options.Customer);
                case "compare":
                    var models = options.Models
                        .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
                        .ToList();
                    return await Runner.CompareAsync(cfg, strategy, models, !options.NoJudge, options.Limit);
                case "doctor":
                    return await Runner.DoctorAsync(cfg, strategy);
            }
        }
        catch (NotImplementedException ex)
        {
            Console.Error.WriteLine();
            Console.Error.WriteLine($"❌ NotImplementedException: {ex.Message}");
            Console.Error.WriteLine("   Implement that TODO, or switch the corresponding flag in strategy.json back off.");
            return 2;
        }
        catch (ConfigException ex)
        {
            Console.Error.WriteLine();
            Console.Error.WriteLine($"❌ Configuration error: {ex.Message}");
            return 2;
        }
        return 1;
    }
}
