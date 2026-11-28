#pragma once

#include "bot_sdk.hpp"

#include <string>

class Strategy {
public:
    explicit Strategy(const bot_sdk::InitState& init) : init_(init) {}

    std::string act(const bot_sdk::TurnState& state) {
        const auto& me = state.beams[init_.seat];
        const std::string directions = "UDLR";
        const auto forward = directions.find(me.dir);
        constexpr int dx[] = {0, 0, -1, 1};
        constexpr int dy[] = {-1, 1, 0, 0};

        if (me.alive) {
            for (std::size_t offset = 0; offset < directions.size(); ++offset) {
                const auto d = (forward + offset) % directions.size();
                if (d == (forward ^ 1)) {
                    continue;
                }
                bool safe = true;
                const int steps = init_.map.move_steps(me.x, me.y);
                for (int i = 1; i <= steps; ++i) {
                    const int x = me.x + i * dx[d], y = me.y + i * dy[d];
                    if (x < 0 || x >= init_.map.size() || y < 0 || y >= init_.map.size() ||
                        init_.map.terrain()[y][x] == '#' || state.contour[y][x] == '0' + init_.seat) {
                        safe = false;
                        break;
                    }
                }
                if (safe) {
                    return std::string(1, directions[d]);
                }
            }
        }
        return std::string(1, me.dir);
    }

private:
    bot_sdk::InitState init_;
};
