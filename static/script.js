// Aura Recovery Garden - Global JS Helper

document.addEventListener("DOMContentLoaded", () => {
    const csrfMeta = document.querySelector('meta[name="csrf-token"]');
    const csrfToken = csrfMeta ? csrfMeta.getAttribute("content") : "";

    // 1. Plant Habit Form Interceptor
    const formCreateHabit = document.getElementById("form-create-habit");
    if (formCreateHabit) {
        formCreateHabit.addEventListener("submit", async (e) => {
            e.preventDefault();
            
            const btnSubmit = document.getElementById("btn-create-submit");
            const name = document.getElementById("habit-name").value.trim();
            const unit = document.getElementById("habit-unit").value.trim();
            const limit = document.getElementById("habit-limit").value;

            btnSubmit.disabled = true;

            try {
                const response = await fetch("/api/habit/create", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRF-Token": csrfToken
                    },
                    body: JSON.stringify({
                        name: name,
                        unit: unit,
                        daily_limit: limit
                    })
                });

                const data = await response.json();

                if (!response.ok) {
                    throw new Error(data.error || "Failed to create habit.");
                }

                // Success: Reload page to show planted tree in garden
                window.location.reload();

            } catch (err) {
                console.error(err);
                alert(`Could not plant habit: ${err.message}`);
                btnSubmit.disabled = false;
            }
        });
    }

    // 2. Log Activity Form Interceptor
    const formLogActivity = document.getElementById("form-log-activity");
    if (formLogActivity) {
        formLogActivity.addEventListener("submit", async (e) => {
            e.preventDefault();

            const btnSubmit = document.getElementById("btn-log-submit");
            const habitId = document.getElementById("log-select-habit").value;
            const val = document.getElementById("log-value").value;
            const emotion = document.getElementById("log-emotion").value;
            const context = document.getElementById("log-context").value.trim();

            btnSubmit.disabled = true;

            try {
                const response = await fetch("/api/log/create", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRF-Token": csrfToken
                    },
                    body: JSON.stringify({
                        habit_id: habitId,
                        logged_value: val,
                        emotional_state: emotion,
                        trigger_context: context
                    })
                });

                const data = await response.json();

                if (!response.ok) {
                    throw new Error(data.error || "Failed to submit log.");
                }

                // Success: Reload to update tree growth visualization on dashboard
                window.location.reload();

            } catch (err) {
                console.error(err);
                alert(`Could not log progress: ${err.message}`);
                btnSubmit.disabled = false;
            }
        });
    }
});
