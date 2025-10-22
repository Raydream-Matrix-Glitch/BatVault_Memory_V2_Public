import React from "react";

export interface TokenStreamLineProps {
  /**
   * Array of tokens received from the streaming API. These will be
   * concatenated and rendered as a single line of text.
   */
  tokens: string[];
}

/**
 * Render a stream of tokens using a monospaced font. As tokens are appended
 * the line grows; subsequent batches will handle scrolling and cursors.
 */
const TokenStreamLine: React.FC<TokenStreamLineProps> = ({ tokens }) => (
  <pre className="whitespace-pre-wrap font-mono text-copy">
    {Array.isArray(tokens) ? tokens.join("") : String(tokens ?? "")}
  </pre>
);

export default TokenStreamLine;