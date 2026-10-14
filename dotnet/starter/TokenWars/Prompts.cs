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

    // TODO 1.1 – Compact system prompt (Challenge 1 "The Token Diet")
    // The baseline prompt (shared/prompts/system-baseline.md) is hundreds of tokens of verbose instructions and asks
    // for long answers with headings, empathy paragraphs and full policy recaps – you pay for it on EVERY call.
    // Replace null with a short, strict system prompt (use the exact text from the workshop website) that says:
    //   - who the assistant is and to answer using ONLY the context provided,
    //   - be accurate and concise: at most 5 short sentences or bullet points, with exact numbers/fees/deadlines,
    //   - if the context does not contain the answer, offer to connect the customer with a human agent.
    // Then set "compact_prompt": true in strategy.json. (The rest of the compact layout is already wired up below.)
    public static readonly string? CompactSystemPrompt = null;

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

    // TODO 2.3 – Model-specific prompt adaptation (Challenge 2 "Bring Your Own Model")
    // Open-weight and small self-hosted models tend to drift (answer in another language, make up policies).
    // Return the extra instruction "Answer in English. Do not invent policies." for every non-OpenAI model key:
    // "open", "selfhosted", "custom" (coach demo), "fw", and any key that starts with "fw_" (Fireworks)
    // – and "" for every other model. It is appended to the system instructions automatically.
    public static string ModelSpecificInstructions(string modelKey)
    {
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
            if (CompactSystemPrompt == null)
            {
                throw new NotImplementedException("TODO 1.1 not implemented yet – see Prompts.cs");
            }
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

    // TODO 1.7 – Prompt-caching-friendly message layout (Challenge 1 "The Token Diet")
    // Azure OpenAI automatically caches the longest identical PREFIX of a prompt (from 1,024 tokens) and bills cached
    // input tokens much cheaper. The baseline puts "Current date and time" + "Customer ID" at the very TOP of the system
    // message, so the prefix is different on every call and nothing is ever cached.
    // Return [system, user] messages where:
    //   system = ONLY static content:
    //            - CompactPrompt=true : CompactSystemPrompt (+ "\n" + extra if extra is not empty), plus
    //                                   "\n\nKnowledge base:\n" + kbText when strategy.Retrieval == "all"
    //            - CompactPrompt=false: the baseline template WITHOUT the lines containing {{now}} / {{customer_id}}
    //                                   (then .Trim()); fill {{knowledge_base}} with kbText when Retrieval == "all"
    //                                   (else MovedToUser) and {{orders}} with MovedToUser; append "\n\n" + extra if any
    //   user   = $"Customer ID: {customerId}\nToday: {AppInfo.ScenarioToday}\n\nContext:\n{...}\n\nQuestion: {question}"
    //            where the context is ContextBlock(kbText, or "" if the KB is already in the system message, ordersJson)
    // Then set "prompt_cache_friendly": true and watch "cached" input tokens appear in the scorecard (real runs only).
    public static List<ChatMessage> BuildCacheFriendlyMessages(
        Strategy strategy, string template, string extra, string question, string customerId,
        string kbText, string ordersJson)
    {
        throw new NotImplementedException("TODO 1.7 not implemented yet – see Prompts.cs");
    }
}
