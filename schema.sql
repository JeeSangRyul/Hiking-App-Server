-- ============================================================
-- 산담(SanDam) Supabase 스키마
-- Supabase 대시보드 → SQL Editor 에 붙여넣고 실행하세요.
-- auth.users 는 Supabase가 기본 제공(로그인 시 자동 생성).
-- ============================================================

-- 산행 기록 요약
create table if not exists public.hikes (
    id              uuid primary key default gen_random_uuid(),
    user_id         uuid not null references auth.users(id) on delete cascade,
    course_id       text,
    course_name     text not null,
    started_at      timestamptz not null,
    ended_at        timestamptz not null,
    distance_km     double precision not null default 0,
    duration_sec    integer not null default 0,
    cumulative_gain_m integer not null default 0,
    avg_heart_rate  integer,
    weather_summary text,
    created_at      timestamptz not null default now()
);
create index if not exists hikes_user_idx on public.hikes(user_id, started_at desc);

-- 산행 경로 시계열(선택 업로드)
create table if not exists public.trackpoints (
    id        bigserial primary key,
    hike_id   uuid not null references public.hikes(id) on delete cascade,
    t_offset  integer not null,          -- 시작 기준 경과(초)
    latitude  double precision not null,
    longitude double precision not null,
    altitude  double precision
);
create index if not exists trackpoints_hike_idx on public.trackpoints(hike_id);

-- 즐겨찾기
create table if not exists public.favorites (
    user_id    uuid not null references auth.users(id) on delete cascade,
    course_id  text not null,
    course_name text,
    created_at timestamptz not null default now(),
    primary key (user_id, course_id)
);

-- ── RLS: 사용자는 자기 데이터만 접근 ───────────────────────────
-- (FastAPI는 직접 연결로 user_id를 직접 검증하지만, 혹시 모를 직접 접근 대비)
alter table public.hikes       enable row level security;
alter table public.trackpoints enable row level security;
alter table public.favorites   enable row level security;

drop policy if exists "own hikes" on public.hikes;
create policy "own hikes" on public.hikes
    for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists "own favorites" on public.favorites;
create policy "own favorites" on public.favorites
    for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists "own trackpoints" on public.trackpoints;
create policy "own trackpoints" on public.trackpoints
    for all using (
        exists (select 1 from public.hikes h where h.id = hike_id and h.user_id = auth.uid())
    );
