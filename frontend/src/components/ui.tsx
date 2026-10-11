import {
  Alert, Description, Input, Label, ListBox, Select, Spinner, TextArea, TextField,
} from '@heroui/react'
import type { Key } from '@heroui/react'
import type { ReactNode } from 'react'

interface FieldProps {
  label: string
  value: string
  onChange: (value: string) => void
  description?: ReactNode
  placeholder?: string
  type?: string
  name?: string
  isRequired?: boolean
  inputMode?: 'text' | 'decimal' | 'numeric' | 'email'
  list?: string
}

/** Labeled text input (HeroUI TextField + Label + Input + Description). */
export function Field({ label, value, onChange, description, placeholder, type, name, isRequired, inputMode, list }: FieldProps) {
  return (
    <TextField name={name} type={type} value={value} onChange={onChange} isRequired={isRequired} fullWidth>
      <Label>{label}</Label>
      <Input placeholder={placeholder} inputMode={inputMode} list={list} />
      {description && <Description>{description}</Description>}
    </TextField>
  )
}

export function AreaField({ label, value, onChange, description, placeholder, rows = 5, isRequired }:
  Omit<FieldProps, 'type' | 'inputMode' | 'list'> & { rows?: number }) {
  return (
    <TextField value={value} onChange={onChange} isRequired={isRequired} fullWidth>
      <Label>{label}</Label>
      <TextArea rows={rows} placeholder={placeholder} />
      {description && <Description>{description}</Description>}
    </TextField>
  )
}

export interface Option {
  id: string
  label: string
}

/** Single-choice dropdown (HeroUI Select + ListBox). */
export function Choice({ label, value, onChange, options, ariaLabel }: {
  label?: string
  value: string
  onChange: (value: string) => void
  options: Option[]
  ariaLabel?: string
}) {
  return (
    <Select
      aria-label={label ? undefined : ariaLabel}
      value={value as Key}
      onChange={(key) => onChange(String(key ?? ''))}
      fullWidth
    >
      {label && <Label>{label}</Label>}
      <Select.Trigger>
        <Select.Value />
        <Select.Indicator />
      </Select.Trigger>
      <Select.Popover>
        <ListBox>
          {options.map((o) => (
            <ListBox.Item key={o.id} id={o.id} textValue={o.label}>
              {o.label}
              <ListBox.ItemIndicator />
            </ListBox.Item>
          ))}
        </ListBox>
      </Select.Popover>
    </Select>
  )
}

export function ErrorAlert({ title = 'Something went wrong', message }: { title?: string; message: string | null }) {
  if (!message) return null
  return (
    <Alert status="danger">
      <Alert.Indicator />
      <Alert.Content>
        <Alert.Title>{title}</Alert.Title>
        <Alert.Description>{message}</Alert.Description>
      </Alert.Content>
    </Alert>
  )
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 py-12 text-muted">
      <Spinner size="sm" /> {label}
    </div>
  )
}

/** Shown under buttons that call the AI, which can take a minute or more. */
export function SlowNote({ active }: { active: boolean }) {
  if (!active) return null
  return <p className="text-sm text-muted">The AI is reading this. It can take up to a couple of minutes; keep this page open.</p>
}

export function PageTitle({ title, subtitle }: { title: string; subtitle?: ReactNode }) {
  return (
    <div className="mb-6">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      {subtitle && <p className="mt-1 text-muted">{subtitle}</p>}
    </div>
  )
}
