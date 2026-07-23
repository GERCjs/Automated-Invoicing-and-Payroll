(function () {
    const widget = document.querySelector("[data-support-chat]");
    if (!widget) {
        return;
    }

    const panel = widget.querySelector("#support-chat-panel");
    const launcher = widget.querySelector(".support-chat-launcher");
    const closeButton = widget.querySelector(".support-chat-close");
    const form = widget.querySelector("[data-support-chat-form]");
    const messages = widget.querySelector("[data-support-chat-messages]");
    const messageInput = widget.querySelector("[data-support-chat-input]");
    const submitButton = widget.querySelector("[data-support-chat-submit]");
    const categoryInput = widget.querySelector("[data-support-chat-category]");
    const issueInput = widget.querySelector("[data-support-chat-issue]");
    const invoiceIdInput = widget.querySelector("[data-support-chat-invoice-id]");
    const referenceInput = widget.querySelector("[data-support-chat-reference]");
    const optionsScript = widget.querySelector("#support-chat-options");
    const chatOptions = optionsScript ? JSON.parse(optionsScript.textContent) : { options: [], references: [] };
    const responseTargetDays = Number(chatOptions.responseTargetDays || 0);
    const initialMessagesHtml = messages.innerHTML;
    let activeOption = null;
    let awaitingMoreHelp = false;
    let chatEnded = false;
    let hasUserInteracted = false;

    const generalAnswers = [
        {
            keywords: ["pay invoice", "make payment", "how to pay", "payment method", "bank transfer", "card payment"],
            answer: "Open My Invoices, choose the invoice, then use the payment instructions or available payment action on that invoice page.",
        },
        {
            keywords: ["download invoice", "invoice pdf", "save invoice"],
            answer: "Open My Invoices, choose the invoice, then use Download PDF on the invoice page.",
        },
        {
            keywords: ["view invoice", "find invoice", "my invoice", "invoice history"],
            answer: "Open My Invoices to see pending, overdue, and paid invoices linked to your account.",
        },
        {
            keywords: ["calculate invoice", "invoice calculated", "invoice total", "invoice amount", "amount due", "gst", "tax"],
            answer: "Invoice totals are based on the invoice line items, quantities, unit prices, and any GST or tax shown on the invoice. If a specific invoice amount looks wrong, choose Invoice amount is wrong and select that invoice.",
        },
        {
            keywords: ["view payslip", "my payslip", "payslip history", "payroll record"],
            answer: "Open My Payslips to view the payroll records linked to your staff account.",
        },
        {
            keywords: ["download payslip", "payslip pdf", "save payslip"],
            answer: "Open My Payslips and use Download PDF beside the payslip you need.",
        },
        {
            keywords: ["calculate my pay", "how do you calculate my pay", "pay calculated", "salary calculated", "net salary", "gross salary", "deduction", "deductions", "cpf", "allowance", "allowances", "commission"],
            answer: "Your pay is calculated from the payroll record: basic salary plus allowances or commissions, minus deductions and employee CPF. Open My Payslips for the exact breakdown. If a specific payslip looks wrong, choose My payslip looks wrong and select that payslip.",
        },
        {
            keywords: ["reset password", "change password", "forgot password", "update password"],
            answer: "Use the account password reset or ask Admin if you cannot access your account. If you still cannot log in, choose I need account help so Admin can follow up.",
        },
        {
            keywords: ["what is cpf", "cpf meaning", "employee cpf"],
            answer: "CPF is the employee contribution deducted from payroll according to the payroll record. Your payslip shows the CPF amount used for that pay period.",
        },
        {
            keywords: ["why overdue", "overdue invoice", "payment reminder", "reminder email"],
            answer: "Reminder emails are sent based on the saved reminder rules. An invoice is treated as overdue when the due date has passed and payment has not been recorded.",
        },
        {
            keywords: ["ticket status", "support status", "my support", "support request", "request history"],
            answer: "Open My Support Requests to review the tickets you submitted and any resolution notes from the support team.",
        },
        {
            keywords: ["how long", "response time", "resolve", "resolved", "response target"],
            answer: responseTargetDays
                ? `The support team uses a ${responseTargetDays} day response target. Tickets that pass that target are highlighted for the responsible officers.`
                : "The support team uses a configurable response target. Tickets that pass that target are highlighted for the responsible officers.",
        },
        {
            keywords: ["who handles", "finance", "payroll", "hr", "admin"],
            answer: "Finance handles invoice and payment requests. Payroll handles payslip and payroll requests. Admin handles account and general support routing.",
        },
    ];

    function appendMessage(text, type) {
        const bubble = document.createElement("div");
        bubble.className = `support-chat-message support-chat-message-${type}`;
        bubble.textContent = text;
        messages.appendChild(bubble);
        messages.scrollTop = messages.scrollHeight;
    }

    function appendQuickReplies(prompt, replies, onSelect) {
        if (!replies.length) {
            return;
        }

        const group = document.createElement("div");
        group.className = "support-chat-quick-group";

        const promptBubble = document.createElement("div");
        promptBubble.className = "support-chat-message support-chat-message-bot";
        promptBubble.textContent = prompt;
        group.appendChild(promptBubble);

        const chips = document.createElement("div");
        chips.className = "support-chat-quick-replies";
        replies.forEach((reply) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "support-chat-chip";
            button.textContent = reply.meta ? `${reply.label} (${reply.meta})` : reply.label;
            button.addEventListener("click", () => {
                chips.querySelectorAll("button").forEach((chip) => {
                    chip.disabled = true;
                });
                onSelect(reply);
            });
            chips.appendChild(button);
        });
        group.appendChild(chips);
        messages.appendChild(group);
        messages.scrollTop = messages.scrollHeight;
    }

    function resetConversationMetadata() {
        activeOption = null;
        categoryInput.value = "";
        issueInput.value = "";
        invoiceIdInput.value = "";
        referenceInput.value = "";
        messageInput.placeholder = "Write a message...";
    }

    function resetChat() {
        form.reset();
        messageInput.style.height = "";
        resetConversationMetadata();
        awaitingMoreHelp = false;
        chatEnded = false;
        hasUserInteracted = false;
        submitButton.disabled = false;
        submitButton.textContent = "Send";
        messages.innerHTML = initialMessagesHtml;
        showTopicOptions("Choose a topic so we can route this correctly.");
    }

    function setOpen(isOpen) {
        panel.hidden = !isOpen;
        [launcher, closeButton].forEach((toggle) => {
            if (toggle) {
                toggle.setAttribute("aria-expanded", String(isOpen));
            }
        });
        if (isOpen) {
            messageInput.focus();
        }
    }

    function closeChatWithConfirmation() {
        if (panel.hidden) {
            return;
        }
        if (!hasUserInteracted) {
            setOpen(false);
            return;
        }
        if (awaitingMoreHelp) {
            resetChat();
            setOpen(false);
            return;
        }
        const shouldExit = window.confirm("Exit chat? Your current chat will restart.");
        if (!shouldExit) {
            return;
        }
        resetChat();
        setOpen(false);
    }

    function endChat() {
        resetConversationMetadata();
        awaitingMoreHelp = false;
        chatEnded = true;
        appendMessage("Okay, ending this chat. You can reopen it anytime.", "bot");
        window.setTimeout(() => {
            resetChat();
            setOpen(false);
        }, 900);
    }

    function showMoreHelpPrompt(prompt) {
        awaitingMoreHelp = true;
        resetConversationMetadata();
        appendQuickReplies(
            prompt,
            [
                { label: "Yes, I need help", value: "yes" },
                { label: "No, end chat", value: "no" },
            ],
            (reply) => {
                hasUserInteracted = true;
                appendMessage(reply.label, "user");
                if (reply.value === "no") {
                    endChat();
                    return;
                }
                awaitingMoreHelp = false;
                showTopicOptions("Choose a topic so we can route this correctly.");
                messageInput.focus();
            }
        );
    }

    function startOption(option, config) {
        const options = config || {};
        activeOption = option;
        categoryInput.value = option.category || "";
        issueInput.value = option.label || "";
        invoiceIdInput.value = "";
        referenceInput.value = "";
        awaitingMoreHelp = false;
        hasUserInteracted = true;

        if (options.appendUserChoice !== false) {
            appendMessage(option.label, "user");
        }

        const references = option.referenceKind ? chatOptions.references || [] : [];
        if (references.length) {
            appendQuickReplies(option.prompt || "Which record is this about?", references, (reference) => {
                invoiceIdInput.value = reference.id || "";
                referenceInput.value = reference.value || "";
                hasUserInteracted = true;
                appendMessage(reference.label, "user");
                appendMessage(option.detailPrompt || "Tell us more about the issue.", "bot");
                messageInput.placeholder = option.detailPrompt || "Write a message...";
                messageInput.focus();
            });
            return;
        }

        const noReferencePrompt = option.referenceKind === "invoice"
            ? "I could not find a linked invoice to attach here. Open the invoice from My Invoices and use Ask About This Invoice."
            : option.referenceKind
                ? "I could not find linked records to show here."
                : option.prompt || "Tell us more about the issue.";
        appendMessage(noReferencePrompt, "bot");
        messageInput.placeholder = option.detailPrompt || "Write a message...";
        messageInput.focus();
    }

    function showTopicOptions(prompt) {
        awaitingMoreHelp = false;
        appendQuickReplies(prompt, chatOptions.options || [], (option) => {
            startOption(option);
        });
    }

    function normalizeMessage(message) {
        return message.toLowerCase().replace(/[.!?]/g, "").trim();
    }

    function isNegativeResponse(message) {
        return [
            "no",
            "nope",
            "nah",
            "no thanks",
            "no thank you",
            "nothing else",
            "that's all",
            "thats all",
        ].includes(normalizeMessage(message));
    }

    function isAffirmativeResponse(message) {
        return [
            "yes",
            "yeah",
            "yup",
            "sure",
            "ok",
            "okay",
            "i need help",
        ].includes(normalizeMessage(message));
    }

    function includesAny(normalized, keywords) {
        return keywords.some((keyword) => normalized.includes(keyword));
    }

    function optionByLabel(label) {
        return (chatOptions.options || []).find((option) => option.label === label) || null;
    }

    function inferOptionFromMessage(message) {
        const normalized = message.toLowerCase();
        const accountKeywords = ["account", "login", "log in", "password", "profile", "access"];
        if (includesAny(normalized, accountKeywords)) {
            return optionByLabel("I need account help");
        }

        if (chatOptions.role === "staff") {
            if (includesAny(normalized, ["did not receive", "didn't receive", "not paid", "missing pay", "salary missing", "pay missing", "never receive pay"])) {
                return optionByLabel("I did not receive my pay");
            }
            if (includesAny(normalized, ["payslip", "pay slip", "salary", "payroll", "cpf", "deduction", "allowance", "commission", "net salary", "gross salary", "pay amount"])) {
                return optionByLabel("My payslip looks wrong");
            }
        }

        if (chatOptions.role === "customer") {
            if (includesAny(normalized, ["payment", "paid", "pay", "card", "stripe", "receipt", "refund", "failed payment", "payment failed"])) {
                return optionByLabel("I have a payment issue");
            }
            if (includesAny(normalized, ["invoice", "bill", "amount", "total", "gst", "tax", "charge", "overcharged", "wrong amount"])) {
                return optionByLabel("Invoice amount is wrong");
            }
        }

        return null;
    }

    function findGeneralAnswer(message) {
        const normalized = message.toLowerCase();
        const matchedAnswer = generalAnswers.find((entry) => (
            entry.keywords.some((keyword) => normalized.includes(keyword))
        ));
        return matchedAnswer ? matchedAnswer.answer : "";
    }

    function looksLikeSupportIssue(message) {
        const normalized = message.toLowerCase();
        return [
            "wrong",
            "error",
            "issue",
            "problem",
            "failed",
            "cannot",
            "can't",
            "unable",
            "missing",
            "incorrect",
            "did not receive",
            "didn't receive",
        ].some((keyword) => normalized.includes(keyword));
    }

    function clearComposer() {
        form.reset();
        messageInput.style.height = "";
        messageInput.focus();
    }

    function handleNonTicketMessage(message) {
        hasUserInteracted = true;
        appendMessage(message, "user");

        if (isNegativeResponse(message)) {
            clearComposer();
            endChat();
            return;
        }

        if (awaitingMoreHelp && isAffirmativeResponse(message)) {
            awaitingMoreHelp = false;
            clearComposer();
            showTopicOptions("Choose a topic so we can route this correctly.");
            return;
        }

        if (looksLikeSupportIssue(message)) {
            const inferredIssueOption = inferOptionFromMessage(message);
            clearComposer();
            if (inferredIssueOption) {
                startOption(inferredIssueOption, { appendUserChoice: false });
                return;
            }
            appendMessage("It sounds like this may need an officer to check. Please choose a topic first so we can route it correctly.", "bot");
            showTopicOptions("Choose a topic so we can route this correctly.");
            return;
        }

        const generalAnswer = findGeneralAnswer(message);
        if (generalAnswer) {
            appendMessage(generalAnswer, "bot");
            clearComposer();
            showMoreHelpPrompt("Do you need help with anything else?");
            return;
        }

        const inferredOption = inferOptionFromMessage(message);
        if (inferredOption) {
            clearComposer();
            startOption(inferredOption, { appendUserChoice: false });
            return;
        }

        appendMessage("I can answer common questions here. For account-specific issues, choose a topic first so the request is routed to the correct officer.", "bot");
        clearComposer();
        showTopicOptions("Choose a topic so we can route this correctly.");
    }

    if (launcher) {
        launcher.addEventListener("click", () => {
            if (panel.hidden) {
                if (chatEnded) {
                    resetChat();
                }
                setOpen(true);
                return;
            }
            closeChatWithConfirmation();
        });
    }

    if (closeButton) {
        closeButton.addEventListener("click", closeChatWithConfirmation);
    }

    messageInput.addEventListener("input", () => {
        messageInput.style.height = "auto";
        messageInput.style.height = `${Math.min(messageInput.scrollHeight, 92)}px`;
    });

    messageInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            form.requestSubmit();
        }
    });

    form.addEventListener("submit", async (event) => {
        event.preventDefault();

        const formData = new FormData(form);
        const message = String(formData.get("message") || "").trim();
        if (!message) {
            appendMessage("Please type a message before sending.", "bot");
            return;
        }
        hasUserInteracted = true;

        if (!activeOption || awaitingMoreHelp || isNegativeResponse(message)) {
            handleNonTicketMessage(message);
            return;
        }

        appendMessage(message, "user");
        submitButton.disabled = true;
        submitButton.textContent = "Sending...";

        try {
            const response = await fetch(form.action, {
                method: "POST",
                body: formData,
                headers: {
                    "X-Requested-With": "XMLHttpRequest",
                },
            });
            const payload = await response.json();
            if (!response.ok || !payload.ok) {
                appendMessage("I could not send that request. Please check the fields and try again.", "bot");
                return;
            }
            appendMessage(payload.message, "bot");
            clearComposer();
            resetConversationMetadata();
            showMoreHelpPrompt("Need help with anything else?");
        } catch (error) {
            appendMessage("The request could not be sent right now. Please try again in a moment.", "bot");
        } finally {
            submitButton.disabled = false;
            submitButton.textContent = "Send";
        }
    });

    resetChat();
})();
