import { Fragment, useCallback, useState } from 'react';
import { router, useFocusEffect } from 'expo-router';
import { ActivityIndicator, AppState, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { api } from '../../lib/api';
import { describeApiError, messages } from '../../lib/messages';
import { ActionButton, Heading, InlineError, Screen, styles } from '@/components/student-ui';

const text = messages.timetable;
// The same fixed course tokens as the portal; all other styling uses the app theme.
const COLOURS = {
  sky: '#e0f2fe', emerald: '#d1fae5', amber: '#fef3c7', violet: '#ede9fe', teal: '#ccfbf1',
  orange: '#ffedd5', indigo: '#e0e7ff', lime: '#ecfccb', fuchsia: '#fae8ff', cyan: '#cffafe',
};

function campusClock(now, timezone) {
  if (!timezone) return null;
  try {
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', {
      timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
    }).formatToParts(now).map(({ type, value }) => [type, value]));
    const date = `${parts.year}-${parts.month}-${parts.day}`;
    const weekday = new Date(`${date}T00:00:00Z`).getUTCDay();
    return {
      date, day: (weekday + 6) % 7,
      seconds: (Number(parts.hour) % 24) * 3600 + Number(parts.minute) * 60 + Number(parts.second),
    };
  } catch {
    return null;
  }
}

function seconds(time) {
  const [hours, minutes, secondsValue = 0] = String(time).split(':').map(Number);
  return hours * 3600 + minutes * 60 + secondsValue;
}

function blockTimes(cell, periods) {
  const ordinals = cell.periods || [cell.period];
  return { start: periods.get(ordinals[0])?.startTime, end: periods.get(ordinals[ordinals.length - 1])?.endTime };
}

function range(start, end) {
  return text.timeRange(start?.slice(0, 5) || '—', end?.slice(0, 5) || '—');
}

function periodLabel(cell) {
  return cell.span === 2 ? text.labBlock(cell.period, cell.period + 1) : text.period(cell.period);
}

function ClassBlock({ cell, periods, warningIds, badge, highlighted = false }) {
  const times = blockTimes(cell, periods);
  const warning = (cell.slotIds || [cell.slotId]).some((id) => warningIds.has(id));
  return (
    <View style={[
      styles.card, local.cell, { backgroundColor: COLOURS[cell.colour] || '#fff' },
      warning && local.warning, highlighted && local.next,
    ]}>
      {badge ? <Text style={styles.brand}>{badge}</Text> : null}
      <Text style={styles.label}>{periodLabel(cell)} · {range(times.start, times.end)}</Text>
      <Text style={styles.value}>{cell.courseCode}</Text>
      <Text style={styles.body}>{cell.courseTitle}</Text>
      <Text style={styles.label}>{text.room(cell.roomName)}</Text>
      <Text style={styles.brand}>{cell.isLab ? text.lab : text.lecture}</Text>
    </View>
  );
}

export default function Timetable() {
  const [week, setWeek] = useState(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [now, setNow] = useState(() => new Date());
  const [selectedDay, setSelectedDay] = useState(null);

  useFocusEffect(useCallback(() => {
    let active = true;
    setBusy(true);
    setWeek(null);
    setError('');
    setNow(new Date());
    api('/student/timetable')
      .then((payload) => {
        if (!active) return;
        setWeek(payload);
        const clock = campusClock(new Date(), payload.timezone);
        setSelectedDay((day) => day ?? (clock && clock.day < 5 ? clock.day : 0));
      })
      .catch((failure) => { if (active) setError(describeApiError(failure)); })
      .finally(() => { if (active) setBusy(false); });
    const timer = setInterval(() => setNow(new Date()), 15000);
    const subscription = AppState.addEventListener('change', (state) => {
      if (state === 'active') setNow(new Date());
    });
    return () => {
      active = false;
      clearInterval(timer);
      subscription.remove();
    };
  }, [refresh]));

  const periods = new Map((week?.periods || []).map((period) => [period.ordinal, period]));
  const clock = campusClock(now, week?.timezone);
  const insideTerm = Boolean(clock && week?.term && clock.date >= week.term.startDate && clock.date <= week.term.endDate);
  const today = insideTerm ? week.days.find((day) => day.dayOfWeek === clock.day) : null;
  // Ended blocks are omitted; a lab is one block lasting until its second period ends.
  const remaining = (today?.cells || []).filter((cell) => cell.kind === 'SLOT'
    && seconds(blockTimes(cell, periods).end) > clock.seconds)
    .sort((a, b) => a.period - b.period);
  const next = remaining.find((cell) => seconds(blockTimes(cell, periods).start) >= clock.seconds);
  const day = week?.days.find((item) => item.dayOfWeek === (selectedDay ?? 0));
  const warningIds = new Set((week?.warnings || []).flatMap((warning) => warning.slotIds));
  const isEmpty = week && !week.days.some((item) => item.cells.some((cell) => cell.kind === 'SLOT'));
  const visibleCourses = new Set([...remaining, ...(day?.cells || [])]
    .filter((cell) => cell.kind === 'SLOT').map((cell) => cell.courseId));
  const legend = (week?.legend.courses || []).filter((course) => visibleCourses.has(course.id));

  return (
    <Screen>
      <Heading title={text.title} subtitle={text.subtitle} />
      {busy ? (
        <View style={local.loading}>
          <ActivityIndicator color="#173f7a" />
          <Text style={styles.body}>{text.loading}</Text>
        </View>
      ) : null}
      <InlineError message={error} />
      {week ? (
        <>
          {week.target ? <Text style={styles.label}>{week.target.sectionLabel} · {week.term.name}</Text> : null}
          <View style={[styles.card, local.strip]}>
            <Text accessibilityRole="header" style={styles.value}>{text.today}</Text>
            {clock ? (
              remaining.length ? remaining.map((cell) => (
                <ClassBlock key={cell.slotId} cell={cell} periods={periods} warningIds={warningIds}
                  highlighted={cell === next} badge={cell === next ? text.next
                    : seconds(blockTimes(cell, periods).start) < clock.seconds ? text.inProgress : null} />
              )) : <Text style={styles.body}>{week.term && !insideTerm ? text.outsideTerm : text.noRemaining}</Text>
            ) : <Text style={styles.body}>{text.clockUnavailable}</Text>}
            <Text style={styles.body}>{text.timezone(week.timezone)}</Text>
            <Text style={styles.body}>{text.patternNote}</Text>
          </View>
          {isEmpty ? <Text style={styles.body}>{week.note?.error === 'section_unassigned' ? text.unassigned : text.empty}</Text> : null}
          {week.warnings.length ? <Text style={styles.body}>{text.warnings}</Text> : null}
          <Text accessibilityRole="header" style={styles.value}>{text.week}</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={local.days}>
            {text.days.map((label, index) => (
              <Pressable key={label} accessibilityRole="button" accessibilityLabel={text.fullDays[index]}
                accessibilityState={{ selected: index === selectedDay }} onPress={() => setSelectedDay(index)}
                style={({ pressed }) => [styles.button, local.day, index !== selectedDay && styles.secondaryButton, pressed && styles.pressed]}>
                <Text style={[styles.buttonText, index !== selectedDay && styles.secondaryButtonText]}>{label}</Text>
              </Pressable>
            ))}
          </ScrollView>
          <Text accessibilityRole="header" style={styles.label}>{text.fullDays[selectedDay ?? 0]}</Text>
          <View style={local.list}>
            {(day?.cells || []).filter((cell) => cell.kind !== 'COVERED').map((cell) => (
              <Fragment key={cell.period}>
                {cell.period === 4 && week.break ? (
                  <Text style={[styles.body, local.break]}>{text.break(week.break.startTime.slice(0, 5), week.break.endTime.slice(0, 5))}</Text>
                ) : null}
                {cell.kind === 'SLOT' ? (
                  <ClassBlock cell={cell} periods={periods} warningIds={warningIds} />
                ) : (
                  <View style={[styles.card, styles.secondaryButton, local.cell]}>
                    <Text style={styles.label}>{text.period(cell.period)} · {range(periods.get(cell.period)?.startTime, periods.get(cell.period)?.endTime)}</Text>
                    <Text style={styles.body}>{text.emptyPeriod}</Text>
                  </View>
                )}
              </Fragment>
            ))}
          </View>
          <View style={[styles.card, local.strip]}>
            <Text accessibilityRole="header" style={styles.label}>{text.legend}</Text>
            {legend.length ? legend.map((course) => (
              <View key={course.id} style={local.legendRow}>
                <View style={[local.swatch, { backgroundColor: COLOURS[course.colour] || '#fff' }]} />
                <Text style={[styles.body, local.legendText]}>{course.code} · {course.title}</Text>
              </View>
            )) : <Text style={styles.body}>{text.noCourses}</Text>}
          </View>
        </>
      ) : null}
      <ActionButton title={text.refresh} secondary disabled={busy} onPress={() => setRefresh((value) => value + 1)} />
      <ActionButton title={text.home} secondary onPress={() => router.replace('/')} />
    </Screen>
  );
}

const local = StyleSheet.create({
  loading: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  strip: { padding: 16 },
  cell: { padding: 14, gap: 6 },
  next: { borderColor: '#173f7a', borderWidth: 3 },
  warning: { borderColor: '#24344d', borderWidth: 2, borderStyle: 'dashed' },
  days: { gap: 6 },
  day: { minWidth: 52, minHeight: 48, paddingHorizontal: 10, paddingVertical: 12 },
  list: { gap: 10 },
  break: { paddingVertical: 8, textAlign: 'center' },
  legendRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  legendText: { flex: 1 },
  swatch: { width: 20, height: 20, borderRadius: 4, borderWidth: 1, borderColor: '#dbe2ec' },
});
