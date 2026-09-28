/**
 * Read-only value with a copy button. Ported from suitenumerique/messages,
 * except the button stays visible (Messages shows it on hover only, which
 * touch and keyboard users never trigger).
 */

import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@gouvfr-lasuite/ui-components";
import { Checkmark } from "@gouvfr-lasuite/ui-components/icons";

export type CopyableInputProps = Omit<
  React.DetailedHTMLProps<React.InputHTMLAttributes<HTMLInputElement>, HTMLInputElement>,
  "value"
> & {
  value: string;
};

export function CopyableInput({ value, readOnly = true, ...props }: CopyableInputProps) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard denied (insecure context, permissions): the value stays
      // selectable by hand.
    }
  };

  return (
    <div className="copyable-input">
      <input
        type="text"
        readOnly={readOnly}
        {...props}
        value={value}
        // Delayed so the mouseup of a click does not undo the selection.
        onFocus={(event) => {
          const input = event.target;
          setTimeout(() => input.select(), 100);
        }}
        className="copyable-input__input"
      />
      <Button
        size="nano"
        variant="secondary"
        onClick={() => void handleCopy()}
        aria-label={copied ? t("common.copied") : t("common.copy")}
        aria-live="polite"
      >
        {copied ? <Checkmark /> : t("common.copy")}
      </Button>
    </div>
  );
}
