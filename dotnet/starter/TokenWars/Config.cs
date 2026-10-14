using System.Globalization;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace TokenWars;

/// <summary>Constants that identify this app on the leaderboard.</summary>
public static class AppInfo
{
    public const string Language = "dotnet";
    public const string Variant = "starter";

    /// <summary>Fixed "today" of the ByteCart scenario (used in the cache-friendly user message).</summary>
    public const string ScenarioToday = "2026-09-15";
}

/// <summary>Configuration problem that should be shown to the user without a stack trace.</summary>
public sealed class ConfigException : Exception
{
    public ConfigException(string message) : base(message) { }
}

/// <summary>Small helpers for reading/writing System.Text.Json nodes safely.</summary>
public static class JsonUtil
{
    public static readonly JsonSerializerOptions Indented = new()
    {
        WriteIndented = true,
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    public static readonly JsonSerializerOptions Compact = new()
    {
        WriteIndented = false,
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    public static string? Str(JsonNode? node)
    {
        if (node is JsonValue value && value.TryGetValue<string>(out var s))
        {
            return s;
        }
        return null;
    }

    public static int? Int(JsonNode? node)
    {
        if (node is not JsonValue value) return null;
        if (value.TryGetValue<int>(out var i)) return i;
        if (value.TryGetValue<long>(out var l)) return (int)l;
        if (value.TryGetValue<double>(out var d)) return (int)d;
        if (value.TryGetValue<string>(out var s) &&
            int.TryParse(s, NumberStyles.Integer, CultureInfo.InvariantCulture, out var p)) return p;
        return null;
    }

    public static double? Dbl(JsonNode? node)
    {
        if (node is not JsonValue value) return null;
        if (value.TryGetValue<double>(out var d)) return d;
        if (value.TryGetValue<int>(out var i)) return i;
        if (value.TryGetValue<long>(out var l)) return l;
        if (value.TryGetValue<string>(out var s) &&
            double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out var p)) return p;
        return null;
    }

    public static bool? Bool(JsonNode? node)
    {
        if (node is not JsonValue value) return null;
        if (value.TryGetValue<bool>(out var b)) return b;
        if (value.TryGetValue<string>(out var s) && bool.TryParse(s, out var p)) return p;
        return null;
    }

    public static JsonNode? ParseFile(string path)
    {
        try
        {
            return JsonNode.Parse(File.ReadAllText(path));
        }
        catch (JsonException ex)
        {
            throw new ConfigException($"Invalid JSON in {path}: {ex.Message}");
        }
    }

    /// <summary>Rounds money values so the JSON output does not contain floating point noise.</summary>
    public static double Money(double value) => Math.Round(value, 8);
}

/// <summary>The optimisation switches from strategy.json (SPEC 2.5).</summary>
public sealed class Strategy
{
    public bool CompactPrompt { get; set; }
    public bool PromptCacheFriendly { get; set; }
    public string Retrieval { get; set; } = "all";
    public int TopK { get; set; } = 3;
    public bool OrderLookup { get; set; }
    public int? MaxOutputTokens { get; set; }
    public bool ExactCache { get; set; }
    public bool SemanticCache { get; set; }
    public double SemanticCacheThreshold { get; set; } = 0.92;
    public string DefaultModel { get; set; } = "premium";
    public string Routing { get; set; } = "none";
    public bool Escalation { get; set; }
    public bool UseGateway { get; set; } = true;
    public bool RetryOnThrottle { get; set; }
    public int Concurrency { get; set; } = 8;

    public Strategy Clone() => (Strategy)MemberwiseClone();

    public static Strategy Load(string path)
    {
        if (!File.Exists(path)) throw new ConfigException($"strategy.json not found at {path}");
        if (JsonUtil.ParseFile(path) is not JsonObject o)
        {
            throw new ConfigException($"{path} must contain a JSON object.");
        }

        var unknown = o.Select(kv => kv.Key).Where(k => !KnownKeys.Contains(k)).OrderBy(k => k, StringComparer.Ordinal).ToList();
        if (unknown.Count > 0)
        {
            Console.Error.WriteLine($"⚠️  Ignoring unknown strategy.json keys: {string.Join(", ", unknown)}");
        }

        if (o.TryGetPropertyValue("use_gateway", out var useGateway) && useGateway?.GetValueKind() != JsonValueKind.True)
            throw new ConfigException("APIM is required: strategy.use_gateway must be the JSON boolean true.");

        var s = new Strategy
        {
            CompactPrompt = JsonUtil.Bool(o["compact_prompt"]) ?? false,
            PromptCacheFriendly = JsonUtil.Bool(o["prompt_cache_friendly"]) ?? false,
            Retrieval = (JsonUtil.Str(o["retrieval"]) ?? "all").Trim().ToLowerInvariant(),
            TopK = JsonUtil.Int(o["top_k"]) ?? 3,
            OrderLookup = JsonUtil.Bool(o["order_lookup"]) ?? false,
            MaxOutputTokens = JsonUtil.Int(o["max_output_tokens"]),
            ExactCache = JsonUtil.Bool(o["exact_cache"]) ?? false,
            SemanticCache = JsonUtil.Bool(o["semantic_cache"]) ?? false,
            SemanticCacheThreshold = JsonUtil.Dbl(o["semantic_cache_threshold"]) ?? 0.92,
            DefaultModel = (JsonUtil.Str(o["default_model"]) ?? "premium").Trim(),
            Routing = (JsonUtil.Str(o["routing"]) ?? "none").Trim().ToLowerInvariant(),
            Escalation = JsonUtil.Bool(o["escalation"]) ?? false,
            UseGateway = JsonUtil.Bool(o["use_gateway"]) ?? true,
            RetryOnThrottle = JsonUtil.Bool(o["retry_on_throttle"]) ?? false,
            Concurrency = JsonUtil.Int(o["concurrency"]) ?? 8,
        };

        if (!s.UseGateway)
            throw new ConfigException("APIM is required: strategy.use_gateway must be true (the judge is the only direct exception).");
        if (s.Retrieval is not ("all" or "keyword" or "embedding"))
            throw new ConfigException($"strategy.retrieval must be \"all\", \"keyword\" or \"embedding\" (got \"{s.Retrieval}\")");
        if (s.Routing is not ("none" or "rules" or "classifier"))
            throw new ConfigException($"strategy.routing must be \"none\", \"rules\" or \"classifier\" (got \"{s.Routing}\")");
        s.TopK = Math.Max(1, s.TopK);
        s.Concurrency = Math.Max(1, s.Concurrency);
        return s;
    }

    private static readonly HashSet<string> KnownKeys = new(StringComparer.Ordinal)
    {
        "compact_prompt", "prompt_cache_friendly", "retrieval", "top_k", "order_lookup", "max_output_tokens",
        "exact_cache", "semantic_cache", "semantic_cache_threshold", "default_model", "routing", "escalation",
        "use_gateway", "retry_on_throttle", "concurrency",
    };

    public JsonObject ToJson() => new()
    {
        ["compact_prompt"] = CompactPrompt,
        ["prompt_cache_friendly"] = PromptCacheFriendly,
        ["retrieval"] = Retrieval,
        ["top_k"] = TopK,
        ["order_lookup"] = OrderLookup,
        ["max_output_tokens"] = MaxOutputTokens,
        ["exact_cache"] = ExactCache,
        ["semantic_cache"] = SemanticCache,
        ["semantic_cache_threshold"] = SemanticCacheThreshold,
        ["default_model"] = DefaultModel,
        ["routing"] = Routing,
        ["escalation"] = Escalation,
        ["use_gateway"] = UseGateway,
        ["retry_on_throttle"] = RetryOnThrottle,
        ["concurrency"] = Concurrency,
    };
}

/// <summary>One entry of shared/config/models.json (SPEC 2.2).</summary>
public sealed class ModelConfig
{
    public string Key { get; init; } = "";
    public string Deployment { get; init; } = "";
    public string BaseUrl { get; init; } = "";
    public string ApiKeyEnv { get; init; } = "AZURE_AI_API_KEY";
    public string PricingKey { get; init; } = "";
    public string Type { get; init; } = "chat";
    public bool ViaGateway { get; init; } = true;
    public string MaxTokensParam { get; init; } = "max_tokens";

    /// <summary>When false, `temperature` is left out of every chat request (GPT-5.6 family).</summary>
    public bool SupportsTemperature { get; init; } = true;

    /// <summary>extra_body: (key, compact JSON value) pairs merged into every chat request body for this model.</summary>
    public IReadOnlyList<KeyValuePair<string, string>> ExtraBody { get; init; } = Array.Empty<KeyValuePair<string, string>>();

    /// <summary>extra_headers: (name, value) HTTP headers added to every chat/embedding request (e.g. x-session-affinity).</summary>
    public IReadOnlyList<KeyValuePair<string, string>> ExtraHeaders { get; init; } = Array.Empty<KeyValuePair<string, string>>();

    /// <summary>Informational hosting fee (e.g. a fine-tuned deployment); shown by `compare`, never part of the score.</summary>
    public double HourlyCostUsd { get; init; }

    public bool IsChat => Type == "chat";
}

public sealed class GatewayConfig
{
    public string BaseUrl { get; init; } = "";
    public string ApiKeyEnv { get; init; } = "APIM_SUBSCRIPTION_KEY";
}

/// <summary>shared/config/scoring.json (SPEC 2.4).</summary>
public sealed class ScoringConfig
{
    public int PassScore { get; init; } = 4;
    public double MinPassRate { get; init; } = 0.85;
    public string JudgeModel { get; init; } = "judge";
    public int JudgeConcurrency { get; init; } = 8;

    public static ScoringConfig Load(string path)
    {
        if (!File.Exists(path)) return new ScoringConfig();
        var o = JsonUtil.ParseFile(path) as JsonObject;
        return new ScoringConfig
        {
            PassScore = JsonUtil.Int(o?["pass_score"]) ?? 4,
            MinPassRate = JsonUtil.Dbl(o?["min_pass_rate"]) ?? 0.85,
            JudgeModel = JsonUtil.Str(o?["judge_model"]) ?? "judge",
            JudgeConcurrency = Math.Max(1, JsonUtil.Int(o?["judge_concurrency"]) ?? 8),
        };
    }
}

/// <summary>Everything the app needs: paths, .env values, models, pricing, scoring and strategy.</summary>
public sealed class AppConfig
{
    public string Root { get; private init; } = "";
    public string StrategyPath { get; private init; } = "";
    public string AppDir { get; private init; } = "";
    public string ResultsDir { get; private init; } = "";
    public string EnvFilePath { get; private init; } = "";
    public bool EnvFileFound { get; private init; }
    public string ModelsSource { get; private init; } = "";
    public bool Mock { get; private init; }
    public Dictionary<string, ModelConfig> Models { get; private init; } = new();
    public GatewayConfig? Gateway { get; private init; }
    public PricingTable Pricing { get; private init; } = PricingTable.Empty();
    public ScoringConfig Scoring { get; private init; } = new();
    public Strategy Strategy { get; private init; } = new();
    public List<string> Warnings { get; private init; } = new();

    private Dictionary<string, string> DotEnv { get; init; } = new();

    public string Language => AppInfo.Language;
    public string Variant => AppInfo.Variant;

    public ModelConfig Model(string key)
    {
        if (!Models.TryGetValue(key, out var model))
        {
            throw new ConfigException($"Model \"{key}\" is not configured in {ModelsSource}");
        }
        return model;
    }

    public List<string> ChatModels() => Models.Where(kv => kv.Value.Type == "chat").Select(kv => kv.Key).ToList();

    public string Team
    {
        get
        {
            var team = GetEnv("TOKENWARS_TEAM");
            return string.IsNullOrWhiteSpace(team) ? "anonymous" : team.Trim();
        }
    }

    /// <summary>Real environment variables override values from ROOT/.env.</summary>
    public string GetEnv(string name)
    {
        var real = Environment.GetEnvironmentVariable(name);
        if (real != null) return real;
        return DotEnv.TryGetValue(name, out var v) ? v : "";
    }

    public string SharedPath(params string[] parts)
    {
        var all = new List<string> { Root, "shared" };
        all.AddRange(parts);
        return Path.Combine(all.ToArray());
    }

    public static AppConfig Load(bool mockFlag, string? strategyOverride = null)
    {
        var root = FindRoot();
        var envPath = Path.Combine(root, ".env");
        var dotEnv = ParseEnvFile(envPath);

        string EnvValue(string name)
        {
            var real = Environment.GetEnvironmentVariable(name);
            if (real != null) return real;
            return dotEnv.TryGetValue(name, out var v) ? v : "";
        }

        var mockEnv = EnvValue("TOKENWARS_MOCK").Trim().ToLowerInvariant();
        bool mock = mockFlag || mockEnv is "1" or "true" or "yes" or "on";
        var warnings = new List<string>();

        var strategyPath = string.IsNullOrWhiteSpace(strategyOverride)
            ? FindStrategyFile()
            : Path.GetFullPath(strategyOverride);
        var appDir = Path.GetDirectoryName(strategyPath) ?? Directory.GetCurrentDirectory();

        var (models, gateway, source) = LoadModels(root, mock, warnings);

        var pricingPath = Path.Combine(root, "shared", "config", "pricing.json");
        PricingTable pricing;
        if (File.Exists(pricingPath))
        {
            pricing = PricingTable.Load(pricingPath);
        }
        else
        {
            pricing = PricingTable.BuiltIn();
            warnings.Add("pricing.json not found – using built-in list prices");
        }

        var scoringPath = Path.Combine(root, "shared", "config", "scoring.json");
        if (!File.Exists(scoringPath)) warnings.Add("scoring.json not found – using built-in scoring defaults");

        return new AppConfig
        {
            Root = root,
            StrategyPath = strategyPath,
            AppDir = appDir,
            ResultsDir = Path.Combine(appDir, "results"),
            EnvFilePath = envPath,
            EnvFileFound = File.Exists(envPath),
            DotEnv = dotEnv,
            Mock = mock,
            Models = models,
            Gateway = gateway,
            ModelsSource = source,
            Pricing = pricing,
            Scoring = ScoringConfig.Load(scoringPath),
            Strategy = Strategy.Load(strategyPath),
            Warnings = warnings,
        };
    }

    /// <summary>Walks up from the current directory until a folder containing shared/config is found.</summary>
    private static string FindRoot()
    {
        var overrideRoot = Environment.GetEnvironmentVariable("TOKENWARS_ROOT");
        if (!string.IsNullOrWhiteSpace(overrideRoot))
        {
            var full = Path.GetFullPath(overrideRoot);
            if (!Directory.Exists(Path.Combine(full, "shared", "config")))
                throw new ConfigException($"TOKENWARS_ROOT={full} does not contain shared/config.");
            return full;
        }

        foreach (var start in new[] { Directory.GetCurrentDirectory(), AppContext.BaseDirectory })
        {
            var dir = new DirectoryInfo(start);
            while (dir != null)
            {
                if (Directory.Exists(Path.Combine(dir.FullName, "shared", "config"))) return dir.FullName;
                dir = dir.Parent;
            }
        }
        throw new ConfigException(
            "Could not find the repository root (a folder containing shared/config). " +
            "Run the app from inside the repository or set TOKENWARS_ROOT.");
    }

    /// <summary>strategy.json from the current directory, else from the project folder (walking up from the binary).</summary>
    private static string FindStrategyFile()
    {
        var inCwd = Path.Combine(Directory.GetCurrentDirectory(), "strategy.json");
        if (File.Exists(inCwd)) return inCwd;

        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir != null)
        {
            if (dir.GetFiles("*.csproj").Length > 0)
            {
                var candidate = Path.Combine(dir.FullName, "strategy.json");
                if (File.Exists(candidate)) return candidate;
            }
            dir = dir.Parent;
        }
        throw new ConfigException(
            "strategy.json not found. Run the app from the project folder, e.g. `cd dotnet/solution/TokenWars && dotnet run -- run`.");
    }

    /// <summary>Simple KEY=VALUE parser: # comments, optional quotes, optional "export " prefix.</summary>
    private static Dictionary<string, string> ParseEnvFile(string path)
    {
        var result = new Dictionary<string, string>(StringComparer.Ordinal);
        if (!File.Exists(path)) return result;

        foreach (var raw in File.ReadAllLines(path))
        {
            var line = raw.Trim();
            if (line.Length == 0 || line.StartsWith('#')) continue;
            if (line.StartsWith("export ", StringComparison.Ordinal)) line = line.Substring(7).TrimStart();

            int eq = line.IndexOf('=');
            if (eq <= 0) continue;
            var key = line.Substring(0, eq).Trim();
            var value = line.Substring(eq + 1).Trim();

            if (value.Length >= 1 && (value[0] == '"' || value[0] == '\''))
            {
                char quote = value[0];
                int close = value.IndexOf(quote, 1);
                value = close > 0 ? value.Substring(1, close - 1) : value.Substring(1);
            }
            else if (value.StartsWith('#'))
            {
                value = "";
            }
            else
            {
                // strip inline comments ("value   # comment")
                for (int i = 1; i < value.Length; i++)
                {
                    if (value[i] == '#' && char.IsWhiteSpace(value[i - 1]))
                    {
                        value = value.Substring(0, i).TrimEnd();
                        break;
                    }
                }
            }
            result[key] = value;
        }
        return result;
    }

    private static (Dictionary<string, ModelConfig>, GatewayConfig?, string) LoadModels(
        string root, bool mock, List<string> warnings)
    {
        var configDir = Path.Combine(root, "shared", "config");
        var modelsPath = Path.Combine(configDir, "models.json");
        var examplePath = Path.Combine(configDir, "models.example.json");

        string source;
        JsonNode? doc;
        if (File.Exists(modelsPath))
        {
            source = modelsPath;
            doc = JsonUtil.ParseFile(modelsPath);
        }
        else if (mock && File.Exists(examplePath))
        {
            source = examplePath;
            doc = JsonUtil.ParseFile(examplePath);
            warnings.Add("models.json not found – mock mode uses models.example.json");
        }
        else if (mock)
        {
            source = "built-in mock defaults";
            doc = JsonNode.Parse(BuiltInMockModels);
            warnings.Add("models.json not found – mock mode uses built-in model defaults");
        }
        else
        {
            throw new ConfigException(
                $"Missing {modelsPath}. Run `terraform apply` in infra/ (it generates models.json and .env), " +
                "or use --mock / TOKENWARS_MOCK=1 to work offline.");
        }

        var models = new Dictionary<string, ModelConfig>(StringComparer.Ordinal);
        if (doc?["models"] is JsonObject modelsObj)
        {
            foreach (var (key, node) in modelsObj)
            {
                if (node is not JsonObject m) continue;
                var deployment = JsonUtil.Str(m["deployment"]);
                if (string.IsNullOrEmpty(deployment)) continue;
                if (m.TryGetPropertyValue("via_gateway", out var viaGateway)
                    && viaGateway?.GetValueKind() is not (JsonValueKind.True or JsonValueKind.False))
                    throw new ConfigException($"Model \"{key}\": via_gateway must be a JSON boolean.");
                var baseUrl = (JsonUtil.Str(m["base_url"]) ?? "").Trim();
                if (baseUrl.Length > 0 && !baseUrl.EndsWith('/')) baseUrl += "/";
                var maxParam = JsonUtil.Str(m["max_tokens_param"]);
                models[key] = new ModelConfig
                {
                    Key = key,
                    Deployment = deployment,
                    BaseUrl = baseUrl,
                    ApiKeyEnv = NonEmpty(JsonUtil.Str(m["api_key_env"]), "AZURE_AI_API_KEY"),
                    PricingKey = NonEmpty(JsonUtil.Str(m["pricing_key"]), deployment),
                    Type = NonEmpty(JsonUtil.Str(m["type"]), "chat"),
                    ViaGateway = JsonUtil.Bool(m["via_gateway"]) ?? (key != "judge"),
                    MaxTokensParam = string.IsNullOrWhiteSpace(maxParam) ? "max_tokens" : maxParam,
                    SupportsTemperature = AsBool(m["supports_temperature"], true),
                    ExtraBody = m["extra_body"] is JsonObject extra
                        ? extra.Select(kv => new KeyValuePair<string, string>(
                            kv.Key, kv.Value?.ToJsonString(JsonUtil.Compact) ?? "null")).ToArray()
                        : Array.Empty<KeyValuePair<string, string>>(),
                    ExtraHeaders = StringMap(m["extra_headers"]),
                    HourlyCostUsd = JsonUtil.Dbl(m["hourly_cost_usd"]) ?? 0,
                };
            }
        }

        GatewayConfig? gateway = null;
        if (doc?["gateway"] is JsonObject g)
        {
            var gwUrl = (JsonUtil.Str(g["base_url"]) ?? "").Trim();
            if (gwUrl.Length > 0)
            {
                if (!gwUrl.EndsWith('/')) gwUrl += "/";
                gateway = new GatewayConfig
                {
                    BaseUrl = gwUrl,
                    ApiKeyEnv = NonEmpty(JsonUtil.Str(g["api_key_env"]), "APIM_SUBSCRIPTION_KEY"),
                };
            }
        }
        return (models, gateway, source);
    }

    /// <summary>JSON object -> (name, text) pairs; non-string values are kept as compact JSON, nulls are dropped.</summary>
    private static IReadOnlyList<KeyValuePair<string, string>> StringMap(JsonNode? node)
    {
        if (node is not JsonObject map) return Array.Empty<KeyValuePair<string, string>>();
        return map
            .Where(kv => kv.Value != null)
            .Select(kv => new KeyValuePair<string, string>(
                kv.Key, JsonUtil.Str(kv.Value) ?? kv.Value!.ToJsonString(JsonUtil.Compact)))
            .ToArray();
    }

    private static string NonEmpty(string? value, string fallback) => string.IsNullOrEmpty(value) ? fallback : value;

    /// <summary>Like Python's _as_bool: missing/null -> fallback, strings "1/true/yes/on" -> true, numbers != 0 -> true.</summary>
    private static bool AsBool(JsonNode? node, bool fallback)
    {
        if (node is not JsonValue value) return fallback;
        if (value.TryGetValue<bool>(out var b)) return b;
        if (value.TryGetValue<string>(out var s)) return s.Trim().ToLowerInvariant() is "1" or "true" or "yes" or "on";
        var number = JsonUtil.Dbl(value);
        return number.HasValue ? number.Value != 0 : fallback;
    }

    private const string BuiltInMockModels = """
    {
      "models": {
        "premium":  {"deployment": "gpt-5.6-sol", "base_url": "https://mock.invalid/openai/v1/", "api_key_env": "AZURE_AI_API_KEY", "pricing_key": "gpt-5.6-sol", "type": "chat", "via_gateway": true, "max_tokens_param": "max_completion_tokens", "supports_temperature": false, "extra_body": {"reasoning_effort": "none"}},
        "balanced":      {"deployment": "gpt-5.6-terra", "base_url": "https://mock.invalid/openai/v1/", "api_key_env": "AZURE_AI_API_KEY", "pricing_key": "gpt-5.6-terra", "type": "chat", "via_gateway": true, "max_tokens_param": "max_completion_tokens", "supports_temperature": false, "extra_body": {"reasoning_effort": "none"}},
        "economy":      {"deployment": "gpt-5.6-luna", "base_url": "https://mock.invalid/openai/v1/", "api_key_env": "AZURE_AI_API_KEY", "pricing_key": "gpt-5.6-luna", "type": "chat", "via_gateway": true, "max_tokens_param": "max_completion_tokens", "supports_temperature": false, "extra_body": {"reasoning_effort": "none"}},
        "open":      {"deployment": "Llama-3.3-70B-Instruct", "base_url": "https://mock.invalid/openai/v1/", "api_key_env": "AZURE_AI_API_KEY", "pricing_key": "llama-3.3-70b-instruct", "type": "chat", "via_gateway": true, "max_tokens_param": "max_tokens", "supports_temperature": true, "extra_body": {}},
        "embedding": {"deployment": "text-embedding-3-small", "base_url": "https://mock.invalid/openai/v1/", "api_key_env": "AZURE_AI_API_KEY", "pricing_key": "text-embedding-3-small", "type": "embedding", "via_gateway": true, "max_tokens_param": "max_tokens", "supports_temperature": true, "extra_body": {}},
        "judge":     {"deployment": "judge", "base_url": "https://mock.invalid/openai/v1/", "api_key_env": "AZURE_AI_API_KEY", "pricing_key": "gpt-5.5", "type": "chat", "via_gateway": false, "max_tokens_param": "max_completion_tokens", "supports_temperature": false, "extra_body": {"reasoning_effort": "none"}}
      },
      "gateway": null
    }
    """;
}
