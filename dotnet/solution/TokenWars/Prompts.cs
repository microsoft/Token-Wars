using System.Globalization;

namespace TokenWars;

/// <summary>System/user message construction: baseline, compact and prompt-cache-friendly layouts (SPEC 4.3).</summary>
public static class Prompts
{
    /// <summary>Used only if shared/prompts/system-baseline.md is missing.</summary>
    private const string FallbackBaseline =
        "Current date and time: {{now}}\n" +
        "Customer ID: {{customer_id}}\n" +
        "\n" +
        "You are ByteCart's friendly Support Copilot. Always give thorough, detailed answers with headings,\n" +
        "an empathy paragraph and a full recap of every relevant policy.\n" +
        "\n" +
        "# Knowledge base\n" +
        "{{knowledge_base}}\n" +
        "\n" +
        "# Orders database\n" +
        "{{orders}}\n";

    // SOLUTION 1.1 – compact system prompt (identical text in Python and .NET).
    public const string CompactSystemPrompt =
        "You are ByteCart's customer support assistant. Answer using ONLY the context provided.\n" +
        "Be accurate and concise: at most 5 short sentences or bullet points. " +
        "Include exact numbers, fees and deadlines from the context.\n" +
        "If the context does not contain the answer, say you will connect the customer with a human agent.";

    public const string EscalateInstruction =
        "If you are not confident the context fully answers the question, reply with the single word ESCALATE.";

    public const string ClassifierSystemPrompt =
        "Classify the customer support request by the capability needed to answer it well.\n" +
        "SIMPLE = a single fact lookup from a policy (e.g. return window, fees, opening hours).\n" +
        "STANDARD = needs order data or a short empathetic reply combining 1-2 facts.\n" +
        "COMPLEX = needs reasoning across several policies, exceptions or edge cases.\n" +
        "Reply with exactly one word: SIMPLE, STANDARD or COMPLEX.";

    private const string MovedToUser = "(provided in the user message)";

    public static string LoadBaselineTemplate(string root)
    {
        var path = Path.Combine(root, "shared", "prompts", "system-baseline.md");
        return File.Exists(path) ? File.ReadAllText(path).Replace("\r\n", "\n") : FallbackBaseline;
    }

    /// <summary>Fixed "today" of the ByteCart scenario (used in the cache-friendly user message).</summary>
    public static string UtcNowIso() =>
        AppInfo.ScenarioToday + "T" +
        DateTime.UtcNow.ToString("HH:mm:ss", CultureInfo.InvariantCulture) + "Z";

    // SOLUTION 2.3 – model-specific prompt adaptation for every non-OpenAI model:
    // open-weight (open), self-hosted, the coach's fine-tuned "custom" model and Fireworks models (fw / fw_*).
    public static string ModelSpecificInstructions(string modelKey)
    {
        if (modelKey is "open" or "selfhosted" or "custom" or "fw" || modelKey.StartsWith("fw_", StringComparison.Ordinal))
        {
            return "Answer in English. Do not invent policies.";
        }
        return "";
    }

    public static string ExtraInstructions(string modelKey, bool escalation)
    {
        var lines = new List<string> { ModelSpecificInstructions(modelKey) };
        if (escalation && modelKey != "premium")
        {
            lines.Add(EscalateInstruction);
        }
        return string.Join("\n", lines.Where(l => !string.IsNullOrEmpty(l)));
    }

    public static string ContextBlock(string kbText, string ordersJson)
    {
        var parts = new List<string>();
        if (!string.IsNullOrEmpty(kbText)) parts.Add(kbText);
        if (!string.IsNullOrEmpty(ordersJson)) parts.Add("Orders:\n" + ordersJson);
        return parts.Count > 0 ? string.Join("\n\n", parts) : "(no additional context)";
    }

    /// <summary>kbText = retrieved KB text (full KB when retrieval="all"); ordersJson = selected orders ("" = none).</summary>
    public static List<ChatMessage> BuildMessages(
        Strategy strategy, string template, string modelKey, string question, string customerId,
        string kbText, string ordersJson)
    {
        var extra = ExtraInstructions(modelKey, strategy.Escalation);
        if (strategy.PromptCacheFriendly)
        {
            return BuildCacheFriendlyMessages(strategy, template, extra, question, customerId, kbText, ordersJson);
        }

        string system;
        if (strategy.CompactPrompt)
        {
            system = $"Current date and time: {UtcNowIso()}\nCustomer ID: {customerId}\n\n{CompactSystemPrompt}";
            if (extra.Length > 0) system += "\n" + extra;
            system += "\n\nContext:\n" + ContextBlock(kbText, ordersJson);
        }
        else
        {
            system = template
                .Replace("{{now}}", UtcNowIso())
                .Replace("{{customer_id}}", customerId)
                .Replace("{{knowledge_base}}", kbText)
                .Replace("{{orders}}", ordersJson.Length > 0 ? ordersJson : "[]");
            if (extra.Length > 0) system = system.TrimEnd() + "\n\n" + extra;
        }
        return new List<ChatMessage>
        {
            new ChatMessage("system", system),
            new ChatMessage("user", question),
        };
    }

    // SOLUTION 1.7 – prompt-caching-friendly layout: static system message, dynamic data in the user message.
    public static List<ChatMessage> BuildCacheFriendlyMessages(
        Strategy strategy, string template, string extra, string question, string customerId,
        string kbText, string ordersJson)
    {
        bool kbIsStatic = strategy.Retrieval == "all";
        string system;
        if (strategy.CompactPrompt)
        {
            system = CompactSystemPrompt + (extra.Length > 0 ? "\n" + extra : "");
            if (kbIsStatic) system += "\n\nKnowledge base:\n" + kbText;
        }
        else
        {
            var staticLines = template.Split('\n')
                .Where(line => !line.Contains("{{now}}") && !line.Contains("{{customer_id}}"));
            system = string.Join("\n", staticLines).Trim()
                .Replace("{{knowledge_base}}", kbIsStatic ? kbText : MovedToUser)
                .Replace("{{orders}}", MovedToUser);
            if (extra.Length > 0) system += "\n\n" + extra;
        }

        var context = ContextBlock(kbIsStatic ? "" : kbText, ordersJson);
        var user =
            $"Customer ID: {customerId}\n" +
            $"Today: {AppInfo.ScenarioToday}\n\n" +
            $"Context:\n{context}\n\n" +
            $"Question: {question}";
        return new List<ChatMessage>
        {
            new ChatMessage("system", system),
            new ChatMessage("user", user),
        };
    }
}
