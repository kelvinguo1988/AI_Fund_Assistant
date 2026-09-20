/**
 * 信号灯组件 — 红涨绿跌
 */

import React from 'react';
import { Box, Tooltip } from '@mui/material';
import type { SignalDirection } from '../types';
import { STRENGTH_LABELS } from '../utils/format';

interface SignalIndicatorProps {
  direction: SignalDirection;
  strength?: string;
  size?: number;
  showLabel?: boolean;
}

const SIGNAL_CONFIG: Record<SignalDirection, { color: string; label: string; bgClass: string }> = {
  buy: { color: 'var(--signal-buy)', label: '买入', bgClass: 'signal-buy-bg' },
  sell: { color: 'var(--signal-sell)', label: '卖出', bgClass: 'signal-sell-bg' },
  hold: { color: 'var(--signal-hold)', label: '观望', bgClass: 'signal-hold-bg' },
};

const SignalIndicator: React.FC<SignalIndicatorProps> = ({
  direction,
  strength,
  size = 16,
  showLabel = true,
}) => {
  const config = SIGNAL_CONFIG[direction] || SIGNAL_CONFIG.hold;
  const strengthLabel = strength ? STRENGTH_LABELS[strength] || strength : '';

  return (
    <Tooltip title={strengthLabel || config.label}>
      <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
        <Box
          sx={{
            width: size,
            height: size,
            borderRadius: '50%',
            backgroundColor: config.color,
            boxShadow: `0 0 ${size / 4}px ${config.color}`,
            transition: 'all 0.3s ease',
          }}
        />
        {showLabel && (
          <Box
            component="span"
            sx={{
              color: config.color,
              fontWeight: 600,
              fontSize: size * 0.875,
            }}
          >
            {strengthLabel || config.label}
          </Box>
        )}
      </Box>
    </Tooltip>
  );
};

export default SignalIndicator;
