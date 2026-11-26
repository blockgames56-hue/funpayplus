import { useState } from 'react';
import { Copy, LoaderCircle } from 'lucide-react';
import { toast } from 'sonner';
import { request, useAction, useData } from './api';
import { Button, Confirm, Field, Modal } from './ui';
import type { Account } from './types';

interface CopyForm {
  source_id: number;
  node_id: number;
  title: string;
  description: string;
  controls: {
    name: string;
    label: string;
    value: string;
    required: boolean;
    options: { value: string; label: string }[];
  }[];
}
export function CopyLots() {
  const accounts = useData<Account[]>('accounts', '/accounts');
  const action = useAction();
  const [open, setOpen] = useState(false);
  const [accountId, setAccountId] = useState('');
  const [sourceId, setSourceId] = useState('');
  const [preview, setPreview] = useState<CopyForm | null>(null);
  const [price, setPrice] = useState('');
  const [amount, setAmount] = useState('1');
  const [extra, setExtra] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  async function load() {
    setLoading(true);
    setPreview(null);
    try {
      const data = await request<CopyForm>(
        `/copy-lots/form?account_id=${Number(accountId)}&source_id=${Number(sourceId)}`,
      );
      setPreview(data);
      setExtra(Object.fromEntries(data.controls.map((field) => [field.name, field.value])));
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setLoading(false);
    }
  }
  return (
    <Modal
      open={open}
      onOpenChange={setOpen}
      trigger={
        <Button secondary>
          <Copy size={14} />
          Копировать предложение
        </Button>
      }
      title="CopyLots / адаптация"
      description="Публичные тексты копируются в новую форму вашего аккаунта. Проверьте категорию, цену и варианты товара перед публикацией."
    >
      <Field label="Целевой аккаунт">
        <select
          value={accountId}
          onChange={(event) => {
            setAccountId(event.target.value);
            setPreview(null);
          }}
        >
          <option value="">Выберите аккаунт</option>
          {accounts.data?.map((account) => (
            <option key={account.id} value={account.id}>
              {account.title}
            </option>
          ))}
        </select>
      </Field>
      <Field label="ID исходного предложения">
        <input
          value={sourceId}
          type="number"
          min={1}
          onChange={(event) => {
            setSourceId(event.target.value);
            setPreview(null);
          }}
          placeholder="Число после id= в ссылке на лот"
        />
      </Field>
      <Button
        secondary
        disabled={!accountId || !sourceId || loading}
        onClick={() => {
          void load();
        }}
      >
        {loading && <LoaderCircle className="spin" size={14} />}Загрузить форму
      </Button>
      {preview && (
        <div className="copy-editor">
          <div className="note">
            Категория #{preview.node_id}. Изображения и секреты продавца не переносятся.
          </div>
          <Field label="Название">
            <input
              value={preview.title}
              maxLength={255}
              onChange={(e) => setPreview({ ...preview, title: e.target.value })}
            />
          </Field>
          <Field label="Подробное описание">
            <textarea
              rows={5}
              value={preview.description}
              onChange={(e) => setPreview({ ...preview, description: e.target.value })}
            />
          </Field>
          <div className="two-fields">
            <Field label="Ваша цена">
              <input
                type="number"
                min={0.01}
                step={0.01}
                value={price}
                onChange={(e) => setPrice(e.target.value)}
              />
            </Field>
            <Field label="Количество">
              <input
                type="number"
                min={1}
                max={1000000}
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
              />
            </Field>
          </div>
          {preview.controls.map((field) => (
            <Field key={field.name} label={field.label}>
              {field.options.length ? (
                <select
                  required={field.required}
                  value={extra[field.name] || ''}
                  onChange={(e) => setExtra({ ...extra, [field.name]: e.target.value })}
                >
                  {field.options.map((option) => (
                    <option value={option.value} key={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  required={field.required}
                  value={extra[field.name] || ''}
                  onChange={(e) => setExtra({ ...extra, [field.name]: e.target.value })}
                />
              )}
            </Field>
          ))}
          <Confirm
            trigger={
              <Button
                disabled={
                  action.isPending || Number(price) <= 0 || Number(amount) < 1 || !preview.title
                }
              >
                Опубликовать на FunPay
              </Button>
            }
            title="Создать новое предложение?"
            text={`Лот «${preview.title}», цена ${price}, количество ${amount}. Публикация выполнится от выбранного аккаунта.`}
            onConfirm={async () => {
              await action.mutateAsync({
                path: '/copy-lots/publish',
                body: {
                  account_id: Number(accountId),
                  source_id: preview.source_id,
                  title: preview.title,
                  description: preview.description,
                  price,
                  amount: Number(amount),
                  extra,
                },
                success: 'FunPay принял запрос. Синхронизируйте каталог.',
              });
              setOpen(false);
            }}
          />
        </div>
      )}
    </Modal>
  );
}
