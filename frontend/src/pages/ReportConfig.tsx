/**
 * 报告配置页面 — 支持拖拽排序
 */

import React, { useEffect, useState } from 'react';
import {
  Box,
  Typography,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  Switch,
  Paper,
  Snackbar,
  Alert,
  IconButton,
} from '@mui/material';
import { DragIndicator as DragIcon } from '@mui/icons-material';
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { reportApi } from '../api/report';
import type { ReportConfigOut, ReportConfigUpdate } from '../types';

interface SortableItemProps {
  item: ReportConfigOut;
  onToggle: (id: number, enabled: boolean) => void;
}

const SortableItem: React.FC<SortableItemProps> = ({ item, onToggle }) => {
  const { attributes, listeners, setNodeRef, transform, transition } = useSortable({ id: item.id });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  return (
    <ListItem ref={setNodeRef} style={style} sx={{ bgcolor: 'background.paper', mb: 0.5, borderRadius: 1, border: '1px solid #e0e0e0' }}>
      <ListItemIcon {...attributes} {...listeners} sx={{ cursor: 'grab' }}>
        <IconButton size="small"><DragIcon /></IconButton>
      </ListItemIcon>
      <ListItemText primary={item.name} secondary={`标识: ${item.item_key}`} />
      <Switch checked={item.enabled} onChange={(e) => onToggle(item.id, e.target.checked)} size="small" />
    </ListItem>
  );
};

const ReportConfig: React.FC = () => {
  const [items, setItems] = useState<ReportConfigOut[]>([]);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' as 'success' | 'error' });

  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const loadItems = async () => {
    try {
      const res = await reportApi.list();
      if (res.data) {
        setItems(res.data.sort((a, b) => a.sort_order - b.sort_order));
      }
    } catch {
      setSnackbar({ open: true, message: '加载报告配置失败', severity: 'error' });
    }
  };

  useEffect(() => { loadItems(); }, []);

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    const oldIndex = items.findIndex((i) => i.id === active.id);
    const newIndex = items.findIndex((i) => i.id === over.id);
    const newItems = arrayMove(items, oldIndex, newIndex).map((item, idx) => ({
      ...item,
      sort_order: idx + 1,
    }));
    setItems(newItems);
    saveChanges(newItems);
  };

  const handleToggle = (id: number, enabled: boolean) => {
    const newItems = items.map((i) => i.id === id ? { ...i, enabled } : i);
    setItems(newItems);
    saveChanges(newItems);
  };

  const saveChanges = async (newItems: ReportConfigOut[]) => {
    try {
      const updates: ReportConfigUpdate[] = newItems.map((item) => ({
        id: item.id,
        enabled: item.enabled,
        sort_order: item.sort_order,
      }));
      await reportApi.batchUpdate(updates);
      setSnackbar({ open: true, message: '配置已保存', severity: 'success' });
    } catch {
      setSnackbar({ open: true, message: '保存配置失败', severity: 'error' });
    }
  };

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h5" sx={{ mb: 2 }}>报告配置</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        拖拽调整报告内容项的顺序，开关控制是否包含在报告中
      </Typography>
      <Paper sx={{ p: 2, maxWidth: 600 }}>
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
          <SortableContext items={items.map((i) => i.id)} strategy={verticalListSortingStrategy}>
            <List>
              {items.map((item) => (
                <SortableItem key={item.id} item={item} onToggle={handleToggle} />
              ))}
            </List>
          </SortableContext>
        </DndContext>
      </Paper>

      <Snackbar open={snackbar.open} autoHideDuration={3000} onClose={() => setSnackbar({ ...snackbar, open: false })}>
        <Alert severity={snackbar.severity}>{snackbar.message}</Alert>
      </Snackbar>
    </Box>
  );
};

export default ReportConfig;
