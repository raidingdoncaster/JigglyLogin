(function (window, document) {
  "use strict";

  var LEET_MAP = {
    "@": "a",
    "4": "a",
    "8": "b",
    "3": "e",
    "1": "i",
    "!": "i",
    "|": "i",
    "0": "o",
    "$": "s",
    "5": "s",
    "7": "t",
    "+": "t",
  };
  var LEET_REGEX = new RegExp("[" + Object.keys(LEET_MAP).join("").replace(/[-/\\^$*+?.()|[\]{}]/g, "\\$&") + "]", "g");

  var RULES = [
    {
      id: "self-harm",
      label: "Self-harm references",
      category: "self_harm",
      severity: "critical",
      patterns: [
        "\\bkill\\s+(?:myself|ourselves|yourself|himself|herself)\\b",
        "\\b(?:commit|consider|planning)\\s+suicide\\b",
        "\\bself[-\\s]?harm\\b",
        "\\b(?:end|take)\\s+(?:my|your|their)\\s+life\\b",
        "\\bkms\\b",
        "\\bunalive\\b",
        "\\bsuicidal\\b",
      ],
    },
    {
      id: "violent-threats",
      label: "Violent threats",
      category: "violence",
      severity: "critical",
      patterns: [
        "\\bi['’]m\\s+going\\s+to\\s+(?:kill|murder|hurt|stab|beat)\\s+you\\b",
        "\\b(?:kill|murder|shoot|stab|beat)\\s+(?:you|him|her|them)\\b",
        "\\bbeat\\s+you\\s+to\\s+death\\b",
        "\\b(?:set|light)\\s+you\\s+on\\s+fire\\b",
      ],
    },
    {
      id: "hate-speech",
      label: "Hate speech",
      category: "hate_speech",
      severity: "critical",
      patterns: [
        "\\b(?:kill|hurt|eliminate|erase|attack)\\b[^.]{0,40}\\b(?:jews?|muslims?|christians?|asians?|blacks?|latinos?|immigrants?|gays?|lesbians?|trans|transgender|women|men)\\b",
        "\\b(?:i|we)\\s+hate\\s+(?:jews?|muslims?|asians?|blacks?|latinos?|gays?|lesbians?|trans|immigrants?)\\b",
        "\\bgo back to where you came from\\b",
      ],
    },
    {
      id: "hate-slurs-ethnic",
      label: "Hate speech (ethnic slur)",
      category: "hate_speech",
      severity: "critical",
      patterns: [
        "\\bnigg(?:a|er)s?\\b",
        "\\bspic(?:s|es)?\\b",
        "\\bkike?s?\\b",
        "\\bchink?s?\\b",
        "\\bgook?s?\\b",
        "\\bwetback?s?\\b",
        "\\bsandnigg(?:a|er)s?\\b",
        "\\braghead?s?\\b",
        "\\bcoon?s?\\b",
        "\\btowelhead?s?\\b",
        "\\bporch\\s*monkey\\b",
        "\\bzipper\\s*head\\b",
      ],
    },
    {
      id: "hate-slurs-lgbt",
      label: "Hate speech (LGBTQ+ slur)",
      category: "hate_speech",
      severity: "critical",
      patterns: [
        "\\bfag+(?:ot)?s?\\b",
        "\\bdyke?s?\\b",
        "\\btrann(?:y|ies)\\b",
        "\\bshemale?s?\\b",
        "\\bno\\s+homo\\b",
      ],
    },
    {
      id: "hate-slurs-ableist",
      label: "Hate speech (ableist slur)",
      category: "hate_speech",
      severity: "critical",
      patterns: [
        "\\bretard(?:ed|s)?\\b",
        "\\bree?tard\\b",
        "\\bmongoloid\\b",
        "\\bspaz\\b",
        "\\bcripple\\b",
      ],
    },
    {
      id: "explicit-profanity",
      label: "Severe profanity",
      category: "profanity",
      severity: "high",
      patterns: [
        "\\bf+u+c+k+\\b",
        "\\bmotherf+u+c+ker\\b",
        "\\bshit+\\b",
        "\\bbitch(?:es)?\\b",
        "\\bba?stard\\b",
        "\\basshole\\b",
        "\\bdumbass\\b",
        "\\bjackass\\b",
        "\\bdickhead\\b",
        "\\bdick\\b",
        "\\bcock\\b",
        "\\bcunt\\b",
        "\\bprick\\b",
        "\\bpuss(?:y|ies)\\b",
        "\\bwhore\\b",
        "\\bslut\\b",
        "\\bskank\\b",
        "\\bpiss(?:ed|ing)?\\b",
        "\\bgoddamn\\b",
        "\\bhell\\s+no\\b",
      ],
    },
    {
      id: "sexual-content",
      label: "Sexual or explicit content",
      category: "inappropriate",
      severity: "high",
      patterns: [
        "\\b(?:send|share)\\s+nudes\\b",
        "\\bonlyfans\\b",
        "\\b(?:horny|sexting)\\b",
        "\\bblowjob\\b",
        "\\bhandjob\\b",
        "\\bfellatio\\b",
        "\\bcum(?:shot|ming)?\\b",
        "\\bjizz\\b",
        "\\btit(?:ty)?fuck\\b",
        "\\bbutt\\s+plug\\b",
      ],
    },
    {
      id: "mild-profanity",
      label: "Inappropriate language",
      category: "profanity",
      severity: "medium",
      patterns: [
        "\\bcrap\\b",
        "\\bdamn\\b",
        "\\bhell\\b",
      ],
    },
  ];

  var COMPILED_RULES = RULES.map(function (rule) {
    return {
      id: rule.id,
      label: rule.label,
      category: rule.category,
      severity: rule.severity,
      regexes: rule.patterns.map(function (pattern) {
        return new RegExp(pattern, "i");
      }),
    };
  });

  var PHONE_PATTERN = /(?:\+?\d[\s().-]*){7,}\d/;

  function normalize(value) {
    return (value || "")
      .toLowerCase()
      .replace(LEET_REGEX, function (char) {
        return LEET_MAP[char] || char;
      })
      .replace(/\s+/g, " ");
  }

  function detectPhoneNumber(value) {
    if (!value) return null;
    var match = value.match(PHONE_PATTERN);
    if (!match) return null;
    var digits = match[0].replace(/\D/g, "");
    if (digits.length < 7) return null;
    return match[0].trim();
  }

  function analyze(value) {
    if (!value) {
      return { allowed: true };
    }
    var normalized = normalize(value);
    for (var i = 0; i < COMPILED_RULES.length; i += 1) {
      var rule = COMPILED_RULES[i];
      for (var j = 0; j < rule.regexes.length; j += 1) {
        var regex = rule.regexes[j];
        var hit = regex.exec(normalized);
        if (hit) {
          return {
            allowed: false,
            match: hit[0].trim(),
            severity: rule.severity,
            category: rule.category,
            label: rule.label,
            ruleId: rule.id,
          };
        }
      }
    }
    var phoneHit = detectPhoneNumber(value);
    if (phoneHit) {
      return {
        allowed: false,
        match: phoneHit,
        severity: "critical",
        category: "contact_sharing",
        label: "Phone numbers are not allowed here.",
        ruleId: "phone-number",
      };
    }
    return { allowed: true };
  }

  function scan(value) {
    return new Promise(function (resolve) {
      var started = performance.now();
      var decision = analyze(value);
      var artificialDelay = decision.allowed ? 120 : 320;
      var elapsed = performance.now() - started;
      var delay = Math.max(0, artificialDelay - elapsed);
      window.setTimeout(function () {
        resolve(decision);
      }, delay);
    });
  }

  function modalElements() {
    if (modalElements.cache) {
      return modalElements.cache;
    }
    var modal = document.querySelector("[data-content-filter-modal]");
    if (!modal) {
      modalElements.cache = null;
      return null;
    }
    modalElements.cache = {
      modal: modal,
      match: modal.querySelector("[data-filter-match]"),
      severity: modal.querySelector("[data-filter-severity]"),
      dismissButtons: modal.querySelectorAll("[data-content-filter-dismiss]"),
    };
    return modalElements.cache;
  }

  function showWarning(decision) {
    if (!decision || decision.allowed) return;
    var refs = modalElements();
    if (!refs) return;
    refs.modal.classList.add("is-open");
    refs.modal.removeAttribute("hidden");
    if (refs.match) {
      refs.match.textContent = decision.match || "phrase";
    }
    if (refs.severity) {
      refs.severity.textContent = decision.label || decision.severity || "Blocked";
    }
  }

  function hideWarning() {
    var refs = modalElements();
    if (!refs) return;
    refs.modal.classList.remove("is-open");
    refs.modal.setAttribute("hidden", "hidden");
  }

  document.addEventListener("click", function (event) {
    var refs = modalElements();
    if (!refs || !refs.modal.classList.contains("is-open")) return;
    if (event.target.matches("[data-content-filter-dismiss]")) {
      hideWarning();
      return;
    }
    if (event.target === refs.modal) {
      hideWarning();
    }
  });

  document.addEventListener("keydown", function (evt) {
    if (evt.key === "Escape") {
      hideWarning();
    }
  });

  window.RDABContentFilter = {
    scan: scan,
    analyzeSync: analyze,
    showWarning: showWarning,
    hideWarning: hideWarning,
    policyUrl: window.__COMMUNITY_STANDARDS_URL || "/policies/community-standards",
  };
})(window, document);
