// heron_recorder: record raw IQ samples from one USRP to segment files.
//
// One process records one SDR (D-018). The Python supervisor launches
// one process per SDR and passes every setting on the command line, so
// this program has no config file. It prints one JSON line per event
// on stdout; the supervisor reads them (see recorder_process.py):
//
//   {"event":"ready", ...}     device configured, locks checked
//   {"event":"started", ...}   first sample received
//   {"event":"status", ...}    once per --status-interval seconds
//   {"event":"segment", ...}   a segment file closed
//   {"event":"error","message":"..."}  fatal problem; exit code 1 follows
//   {"event":"stopped", ...}   clean stop after SIGTERM/SIGINT
//
// Design (adapted from the legacy SURGE rx_multi_to_file, which needed
// headers we do not have):
//
// - A receive thread pulls samples from UHD into chunks from a pool
//   (a ring buffer of --buffer-seconds). A writer thread writes chunks
//   to disk. A slow disk cannot block the USB path; when the pool is
//   empty the receive thread drops the chunk and counts a host_drop.
// - Files are cut every --segment-seconds worth of samples (O16), one
//   file per channel:  <outdir>/<sdr-id>/<channel-id>/<prefix>_<seq>.<ext>
//   Each file gets a JSON sidecar with the device time of its first
//   sample, its sample count, and the overflow count inside it.
// - Time alignment across processes (D-020): with a PPS time source,
//   every process waits for the PPS edge of the common --sync-epoch
//   second, sets its device clock to sync-epoch + 1 at that edge, and
//   starts streaming at the common --start-time. All units then share
//   one time base to within one sample clock.
//
// Build: see README.md in this directory (UHD 4.x, Boost, CMake).
// Status: written from the UHD API docs; NOT YET BUILT OR RUN ON
// HARDWARE. Run TEST_BENCH_ROUTINE.md section 3 before any flight.

#include <uhd/exception.hpp>
#include <uhd/types/tune_request.hpp>
#include <uhd/usrp/multi_usrp.hpp>
#include <uhd/utils/safe_main.hpp>
#include <uhd/utils/thread.hpp>

#include <boost/program_options.hpp>

#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <deque>
#include <filesystem>
#include <iostream>
#include <map>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include <fcntl.h>
#include <unistd.h>

namespace po = boost::program_options;
namespace fs = std::filesystem;

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

static std::atomic<bool> g_stop{false};

static void on_signal(int) { g_stop = true; }

// UNIX time in seconds from the host clock.
static double host_now() {
    using namespace std::chrono;
    return duration<double>(system_clock::now().time_since_epoch()).count();
}

static void sleep_s(double seconds) {
    std::this_thread::sleep_for(std::chrono::duration<double>(seconds));
}

// Escape a string for JSON output.
static std::string json_escape(const std::string& in) {
    std::string out;
    for (char c : in) {
        switch (c) {
        case '"': out += "\\\""; break;
        case '\\': out += "\\\\"; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        default: out += c;
        }
    }
    return out;
}

// Print one JSON line and flush, so the supervisor sees it at once.
static std::mutex g_emit_mutex;
static void emit(const std::string& json) {
    std::lock_guard<std::mutex> lock(g_emit_mutex);
    std::fputs(json.c_str(), stdout);
    std::fputc('\n', stdout);
    std::fflush(stdout);
}

static void emit_error(const std::string& message) {
    emit("{\"event\":\"error\",\"message\":\"" + json_escape(message) + "\"}");
}

// ---------------------------------------------------------------------------
// Options
// ---------------------------------------------------------------------------

struct ChannelSpec {
    size_t index = 0;
    std::string id;
    double freq = 0.0;
    double gain = 0.0;
    double bw = 0.0;
    std::string antenna = "RX2";
    std::string subdev;
};

// Parse "index=0,id=L5_direct,freq=1176.45e6,gain=45,bw=20e6,antenna=RX2".
static ChannelSpec parse_channel(const std::string& text) {
    ChannelSpec spec;
    std::map<std::string, std::string> kv;
    std::stringstream ss(text);
    std::string item;
    while (std::getline(ss, item, ',')) {
        auto eq = item.find('=');
        if (eq == std::string::npos) throw std::runtime_error("bad --channel item: " + item);
        kv[item.substr(0, eq)] = item.substr(eq + 1);
    }
    auto need = [&](const std::string& key) {
        auto it = kv.find(key);
        if (it == kv.end()) throw std::runtime_error("--channel is missing " + key);
        return it->second;
    };
    spec.index = std::stoul(need("index"));
    spec.id = need("id");
    spec.freq = std::stod(need("freq"));
    spec.gain = std::stod(need("gain"));
    spec.bw = std::stod(need("bw"));
    if (kv.count("antenna")) spec.antenna = kv["antenna"];
    if (kv.count("subdev")) spec.subdev = kv["subdev"];
    if (spec.id.empty()) throw std::runtime_error("--channel id is empty");
    return spec;
}

struct Options {
    std::string args;
    double rate = 0.0;
    std::string clock_source = "external";
    std::string time_source = "external";
    double sync_epoch = 0.0;
    double start_time = 0.0;
    double segment_seconds = 30.0;
    std::string cpu_format = "sc8";
    std::string wire_format = "sc16";
    double wire_peak = 0.25;
    double buffer_seconds = 4.0;
    double status_interval = 1.0;
    double lock_timeout = 10.0;
    std::string outdir;
    std::string file_prefix;
    std::string file_extension = "sc8";
    std::string sdr_id;
    std::vector<std::string> channel_texts;
    std::vector<ChannelSpec> channels;
};

static size_t bytes_per_sample(const std::string& cpu_format) {
    if (cpu_format == "sc8") return 2;
    if (cpu_format == "sc16") return 4;
    throw std::runtime_error("unsupported cpu format: " + cpu_format);
}

// ---------------------------------------------------------------------------
// Chunk pool: fixed buffers shared by the receive and writer threads
// ---------------------------------------------------------------------------

struct Chunk {
    std::vector<std::vector<char>> data;  // One buffer per channel.
    size_t nsamps = 0;
    double time_spec = 0.0;
    bool has_time = false;
    uint64_t overflows_at = 0;   // Overflow count when received.
    uint64_t host_drops_at = 0;
};

class ChunkPool {
public:
    ChunkPool(size_t count, size_t channels, size_t bytes_per_chunk) : chunks_(count) {
        for (size_t i = 0; i < count; ++i) {
            chunks_[i].data.assign(channels, std::vector<char>(bytes_per_chunk));
            free_.push_back(i);
        }
    }

    // Receive side: take a free chunk, or -1 when the writer is behind.
    long take_free() {
        std::lock_guard<std::mutex> lock(mutex_);
        if (free_.empty()) return -1;
        long idx = static_cast<long>(free_.front());
        free_.pop_front();
        return idx;
    }

    void push_ready(size_t idx) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            ready_.push_back(idx);
        }
        cv_.notify_one();
    }

    // Writer side: wait for a ready chunk, or -1 when finished and empty.
    long wait_ready() {
        std::unique_lock<std::mutex> lock(mutex_);
        cv_.wait(lock, [&] { return !ready_.empty() || finished_; });
        if (ready_.empty()) return -1;
        long idx = static_cast<long>(ready_.front());
        ready_.pop_front();
        return idx;
    }

    void give_back(size_t idx) {
        std::lock_guard<std::mutex> lock(mutex_);
        free_.push_back(idx);
    }

    void finish() {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            finished_ = true;
        }
        cv_.notify_all();
    }

    Chunk& at(size_t idx) { return chunks_[idx]; }
    size_t size() const { return chunks_.size(); }

private:
    std::vector<Chunk> chunks_;
    std::deque<size_t> free_, ready_;
    std::mutex mutex_;
    std::condition_variable cv_;
    bool finished_ = false;
};

// ---------------------------------------------------------------------------
// Segment writer: one channel, files of fixed sample count, with sidecars
// ---------------------------------------------------------------------------

class SegmentWriter {
public:
    SegmentWriter(const Options& opt, const ChannelSpec& ch, size_t bps, uint64_t segment_samples)
        : opt_(opt), ch_(ch), bps_(bps), segment_samples_(segment_samples) {
        dir_ = fs::path(opt.outdir) / opt.sdr_id / ch.id;
        fs::create_directories(dir_);
    }

    ~SegmentWriter() { close(false); }

    // Write nsamps samples. Cut a new segment exactly at the sample count.
    void write(const char* data, size_t nsamps, const Chunk& chunk) {
        size_t offset = 0;
        while (offset < nsamps) {
            if (fp_ == nullptr) open_next(chunk, offset);
            size_t room = static_cast<size_t>(segment_samples_ - in_segment_);
            size_t take = std::min(room, nsamps - offset);
            size_t written = std::fwrite(data + offset * bps_, bps_, take, fp_);
            if (written != take) {
                throw std::runtime_error("disk write failed on " + current_name_ + ": " + std::strerror(errno));
            }
            in_segment_ += written;
            total_samples_ += written;
            total_bytes_ += written * bps_;
            offset += written;
            last_overflows_ = chunk.overflows_at;
            last_drops_ = chunk.host_drops_at;
            if (in_segment_ >= segment_samples_) close(true);
        }
    }

    void close(bool complete) {
        if (fp_ == nullptr) return;
        std::fflush(fp_);
        ::fsync(::fileno(fp_));  // Power loss loses at most this segment (O16).
        std::fclose(fp_);
        fp_ = nullptr;
        write_sidecar(complete);
        emit("{\"event\":\"segment\",\"channel\":\"" + json_escape(ch_.id) + "\",\"seq\":" +
             std::to_string(seq_) + ",\"samples\":" + std::to_string(in_segment_) +
             ",\"complete\":" + (complete ? "true" : "false") + "}");
        ++seq_;
        in_segment_ = 0;
    }

    uint64_t segment_index() const { return seq_; }
    uint64_t total_samples() const { return total_samples_; }
    uint64_t total_bytes() const { return total_bytes_; }

private:
    void open_next(const Chunk& chunk, size_t offset) {
        char num[16];
        std::snprintf(num, sizeof(num), "%05llu", static_cast<unsigned long long>(seq_));
        current_name_ = (dir_ / (opt_.file_prefix + "_" + num + "." + opt_.file_extension)).string();
        fp_ = std::fopen(current_name_.c_str(), "wb");
        if (fp_ == nullptr) throw std::runtime_error("cannot open " + current_name_ + ": " + std::strerror(errno));
        std::setvbuf(fp_, nullptr, _IOFBF, 4 << 20);
        first_time_ = chunk.has_time ? chunk.time_spec + static_cast<double>(offset) / opt_.rate : -1.0;
        first_host_time_ = host_now();
        overflows_at_open_ = chunk.overflows_at;
        drops_at_open_ = chunk.host_drops_at;
    }

    void write_sidecar(bool complete) {
        std::string path = current_name_ + ".json";
        std::FILE* f = std::fopen(path.c_str(), "w");
        if (f == nullptr) return;
        std::fprintf(f,
            "{\n"
            "  \"sdr_id\": \"%s\",\n  \"channel_id\": \"%s\",\n  \"file\": \"%s\",\n  \"seq\": %llu,\n"
            "  \"first_sample_device_time\": %.9f,\n  \"first_sample_host_time\": %.6f,\n"
            "  \"num_samples\": %llu,\n  \"sample_rate_hz\": %.3f,\n  \"center_freq_hz\": %.3f,\n"
            "  \"gain_db\": %.2f,\n  \"bandwidth_hz\": %.3f,\n  \"antenna\": \"%s\",\n"
            "  \"cpu_format\": \"%s\",\n  \"bytes_per_sample\": %zu,\n"
            "  \"overflows_in_segment\": %llu,\n  \"host_drops_in_segment\": %llu,\n  \"complete\": %s\n}\n",
            json_escape(opt_.sdr_id).c_str(), json_escape(ch_.id).c_str(),
            json_escape(fs::path(current_name_).filename().string()).c_str(),
            static_cast<unsigned long long>(seq_), first_time_, first_host_time_,
            static_cast<unsigned long long>(in_segment_), opt_.rate, ch_.freq, ch_.gain, ch_.bw,
            json_escape(ch_.antenna).c_str(), opt_.cpu_format.c_str(), bps_,
            static_cast<unsigned long long>(last_overflows_ - overflows_at_open_),
            static_cast<unsigned long long>(last_drops_ - drops_at_open_),
            complete ? "true" : "false");
        std::fclose(f);
    }

    const Options& opt_;
    ChannelSpec ch_;
    size_t bps_;
    uint64_t segment_samples_;
    fs::path dir_;
    std::FILE* fp_ = nullptr;
    std::string current_name_;
    uint64_t seq_ = 0;
    uint64_t in_segment_ = 0;
    uint64_t total_samples_ = 0;
    uint64_t total_bytes_ = 0;
    double first_time_ = -1.0;
    double first_host_time_ = 0.0;
    uint64_t overflows_at_open_ = 0, drops_at_open_ = 0;
    uint64_t last_overflows_ = 0, last_drops_ = 0;
};

// ---------------------------------------------------------------------------
// Device setup
// ---------------------------------------------------------------------------

// Wait until a sensor reads true, or the timeout passes. Return the value.
static bool wait_for_lock(std::function<bool()> read, double timeout_s) {
    double deadline = host_now() + timeout_s;
    while (host_now() < deadline && !g_stop) {
        try {
            if (read()) return true;
        } catch (const uhd::exception&) {
            return false;
        }
        sleep_s(0.1);
    }
    return false;
}

static bool check_ref_locked(uhd::usrp::multi_usrp::sptr usrp) {
    auto names = usrp->get_mboard_sensor_names(0);
    if (std::find(names.begin(), names.end(), "ref_locked") == names.end()) return true;
    return usrp->get_mboard_sensor("ref_locked", 0).to_bool();
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

int UHD_SAFE_MAIN(int argc, char* argv[]) {
    Options opt;
    po::options_description desc("heron_recorder options");
    desc.add_options()
        ("help", "show this help")
        ("args", po::value<std::string>(&opt.args)->required(), "UHD device args, e.g. serial=XXXX")
        ("rate", po::value<double>(&opt.rate)->required(), "sample rate, Hz")
        ("clock-source", po::value<std::string>(&opt.clock_source)->default_value("external"), "internal|external|gpsdo")
        ("time-source", po::value<std::string>(&opt.time_source)->default_value("external"), "none|external|gpsdo")
        ("sync-epoch", po::value<double>(&opt.sync_epoch)->default_value(0.0), "UNIX second of the common PPS edge (0 = none)")
        ("start-time", po::value<double>(&opt.start_time)->default_value(0.0), "UNIX time of the first sample (0 = now+1)")
        ("segment-seconds", po::value<double>(&opt.segment_seconds)->default_value(30.0), "segment file length")
        ("cpu-format", po::value<std::string>(&opt.cpu_format)->default_value("sc8"), "file sample format: sc8|sc16")
        ("wire-format", po::value<std::string>(&opt.wire_format)->default_value("sc16"), "USB sample format: sc8|sc16")
        ("wire-peak", po::value<double>(&opt.wire_peak)->default_value(0.25), "peak for sc8 wire format")
        ("buffer-seconds", po::value<double>(&opt.buffer_seconds)->default_value(4.0), "host ring buffer length")
        ("status-interval", po::value<double>(&opt.status_interval)->default_value(1.0), "status line period, s")
        ("lock-timeout", po::value<double>(&opt.lock_timeout)->default_value(10.0), "wait for ref/LO lock, s")
        ("outdir", po::value<std::string>(&opt.outdir)->required(), "flight directory")
        ("file-prefix", po::value<std::string>(&opt.file_prefix)->required(), "file name prefix (UTC start stamp)")
        ("file-extension", po::value<std::string>(&opt.file_extension)->default_value("sc8"), "file extension")
        ("sdr-id", po::value<std::string>(&opt.sdr_id)->required(), "unit name for the directory")
        ("channel", po::value<std::vector<std::string>>(&opt.channel_texts)->required()->multitoken(),
         "index=N,id=NAME,freq=HZ,gain=DB,bw=HZ,antenna=RX2[,subdev=SPEC]  (repeat per channel)");

    po::variables_map vm;
    try {
        po::store(po::parse_command_line(argc, argv, desc), vm);
        if (vm.count("help")) {
            std::cout << desc << std::endl;
            return 0;
        }
        po::notify(vm);
        for (const auto& text : opt.channel_texts) opt.channels.push_back(parse_channel(text));
        if (opt.channels.empty()) throw std::runtime_error("at least one --channel is required");
        if (opt.rate <= 0 || opt.segment_seconds <= 0 || opt.buffer_seconds <= 0)
            throw std::runtime_error("rate, segment-seconds and buffer-seconds must be > 0");
    } catch (const std::exception& e) {
        emit_error(std::string("bad arguments: ") + e.what());
        return 1;
    }

    std::signal(SIGINT, on_signal);
    std::signal(SIGTERM, on_signal);

    const size_t num_ch = opt.channels.size();
    const size_t bps = bytes_per_sample(opt.cpu_format);
    // 10 ms of samples per chunk: small enough for low latency, large enough for USB efficiency.
    const size_t chunk_samps = static_cast<size_t>(std::max(1.0, opt.rate / 100.0));
    const size_t pool_count = static_cast<size_t>(std::max(4.0, std::ceil(opt.buffer_seconds * 100.0)));
    const uint64_t segment_samples = static_cast<uint64_t>(std::llround(opt.segment_seconds * opt.rate));

    uhd::usrp::multi_usrp::sptr usrp;
    bool ref_locked = false;
    try {
        usrp = uhd::usrp::multi_usrp::make(uhd::device_addr_t(opt.args));
        usrp->set_clock_source(opt.clock_source);
        if (opt.time_source != "none") usrp->set_time_source(opt.time_source);
        usrp->set_rx_rate(opt.rate);
        for (const auto& ch : opt.channels) {
            if (!ch.subdev.empty()) usrp->set_rx_subdev_spec(uhd::usrp::subdev_spec_t(ch.subdev), 0);
            usrp->set_rx_freq(uhd::tune_request_t(ch.freq), ch.index);
            usrp->set_rx_gain(ch.gain, ch.index);
            usrp->set_rx_bandwidth(ch.bw, ch.index);
            usrp->set_rx_antenna(ch.antenna, ch.index);
        }
        // Locks: the shared 10 MHz reference (D-014) and each channel LO.
        if (opt.clock_source != "internal") {
            ref_locked = wait_for_lock([&] { return check_ref_locked(usrp); }, opt.lock_timeout);
            if (!ref_locked) {
                emit_error("reference not locked on clock source " + opt.clock_source);
                return 1;
            }
        } else {
            ref_locked = true;
        }
        for (const auto& ch : opt.channels) {
            auto names = usrp->get_rx_sensor_names(ch.index);
            if (std::find(names.begin(), names.end(), "lo_locked") == names.end()) continue;
            bool lo = wait_for_lock([&] { return usrp->get_rx_sensor("lo_locked", ch.index).to_bool(); }, opt.lock_timeout);
            if (!lo) {
                emit_error("LO not locked on channel " + ch.id);
                return 1;
            }
        }
    } catch (const std::exception& e) {
        emit_error(std::string("device setup failed: ") + e.what());
        return 1;
    }

    {
        std::ostringstream js;
        js.precision(3);
        js << std::fixed << "{\"event\":\"ready\",\"t\":" << host_now() << ",\"ref_locked\":" << (ref_locked ? "true" : "false")
           << ",\"actual_rate\":" << usrp->get_rx_rate() << ",\"channels\":[";
        for (size_t i = 0; i < num_ch; ++i) {
            const auto& ch = opt.channels[i];
            js << (i ? "," : "") << "{\"id\":\"" << json_escape(ch.id) << "\",\"actual_freq\":" << usrp->get_rx_freq(ch.index)
               << ",\"actual_gain\":" << usrp->get_rx_gain(ch.index) << "}";
        }
        js << "],\"chunk_samples\":" << chunk_samps << ",\"pool_chunks\":" << pool_count << "}";
        emit(js.str());
    }

    // ---- Time alignment (D-020) -------------------------------------------
    double stream_start = 0.0;
    double time_offset_s = 0.0;  // device time minus host time after sync
    try {
        if (opt.time_source != "none" && opt.sync_epoch > 0.0) {
            // Wait for the half second before the common epoch, then for the PPS edge.
            while (host_now() < opt.sync_epoch - 0.5 && !g_stop) sleep_s(0.01);
            if (g_stop) return 0;
            const uhd::time_spec_t last_pps = usrp->get_time_last_pps();
            double deadline = host_now() + 2.5;
            while (usrp->get_time_last_pps() == last_pps) {
                if (host_now() > deadline) throw std::runtime_error("no PPS edge seen; check the PPS cable");
                sleep_s(0.02);
            }
            // We are just after the PPS edge of second sync_epoch (host clock within +-0.5 s).
            usrp->set_time_next_pps(uhd::time_spec_t(opt.sync_epoch + 1.0));
            sleep_s(1.3);
            double dev = usrp->get_time_now().get_real_secs();
            time_offset_s = dev - host_now();
            if (std::fabs(time_offset_s) > 1.0) {
                emit("{\"event\":\"warning\",\"message\":\"device time differs from host by " +
                     std::to_string(time_offset_s) + " s; all recorders still share one PPS-aligned time base\"}");
            }
            stream_start = opt.start_time > 0.0 ? opt.start_time : dev + 1.0;
            if (stream_start < dev + 0.3) {
                emit("{\"event\":\"warning\",\"message\":\"start-time already passed; starting in 1 s\"}");
                stream_start = dev + 1.0;
            }
        } else {
            double now = host_now();
            usrp->set_time_now(uhd::time_spec_t(now));
            stream_start = (opt.start_time > now + 0.3) ? opt.start_time : now + 1.0;
        }
    } catch (const std::exception& e) {
        emit_error(std::string("time sync failed: ") + e.what());
        return 1;
    }

    // ---- Streamer ---------------------------------------------------------
    uhd::rx_streamer::sptr rx;
    try {
        uhd::stream_args_t stream_args(opt.cpu_format, opt.wire_format);
        for (const auto& ch : opt.channels) stream_args.channels.push_back(ch.index);
        if (opt.wire_format == "sc8") stream_args.args["peak"] = std::to_string(opt.wire_peak);
        rx = usrp->get_rx_stream(stream_args);
        uhd::stream_cmd_t cmd(uhd::stream_cmd_t::STREAM_MODE_START_CONTINUOUS);
        cmd.stream_now = false;
        cmd.time_spec = uhd::time_spec_t(stream_start);
        rx->issue_stream_cmd(cmd);
    } catch (const std::exception& e) {
        emit_error(std::string("streamer setup failed: ") + e.what());
        return 1;
    }

    // ---- Writer thread ----------------------------------------------------
    ChunkPool pool(pool_count, num_ch, chunk_samps * bps);
    std::vector<std::unique_ptr<SegmentWriter>> writers;
    try {
        for (const auto& ch : opt.channels) writers.emplace_back(new SegmentWriter(opt, ch, bps, segment_samples));
    } catch (const std::exception& e) {
        emit_error(std::string("cannot create output directories: ") + e.what());
        return 1;
    }

    std::atomic<bool> writer_failed{false};
    std::string writer_error;
    std::thread writer([&] {
        try {
            while (true) {
                long idx = pool.wait_ready();
                if (idx < 0) break;
                Chunk& c = pool.at(static_cast<size_t>(idx));
                for (size_t i = 0; i < num_ch; ++i) writers[i]->write(c.data[i].data(), c.nsamps, c);
                pool.give_back(static_cast<size_t>(idx));
            }
        } catch (const std::exception& e) {
            writer_error = e.what();
            writer_failed = true;
            g_stop = true;
        }
    });

    // ---- Receive loop -----------------------------------------------------
    uhd::set_thread_priority_safe();
    uint64_t overflows = 0, host_drops = 0, timeouts = 0, other_errors = 0, samples = 0;
    unsigned consecutive_timeouts = 0;
    bool started = false;
    int exit_code = 0;
    double last_status = host_now();
    uint64_t bytes_at_last_status = 0;
    std::vector<std::vector<char>> scratch(num_ch, std::vector<char>(chunk_samps * bps));
    uhd::rx_metadata_t md;

    while (!g_stop) {
        long idx = pool.take_free();
        std::vector<void*> ptrs(num_ch);
        Chunk* chunk = nullptr;
        if (idx >= 0) {
            chunk = &pool.at(static_cast<size_t>(idx));
            for (size_t i = 0; i < num_ch; ++i) ptrs[i] = chunk->data[i].data();
        } else {
            // Writer is behind: keep the USB moving into scratch and count a drop.
            for (size_t i = 0; i < num_ch; ++i) ptrs[i] = scratch[i].data();
        }

        size_t n = 0;
        try {
            n = rx->recv(ptrs, chunk_samps, md, started ? 1.0 : 5.0, false);
        } catch (const std::exception& e) {
            emit_error(std::string("recv failed: ") + e.what());
            exit_code = 1;
            if (idx >= 0) pool.give_back(static_cast<size_t>(idx));
            break;
        }

        switch (md.error_code) {
        case uhd::rx_metadata_t::ERROR_CODE_NONE:
            consecutive_timeouts = 0;
            break;
        case uhd::rx_metadata_t::ERROR_CODE_TIMEOUT:
            ++timeouts;
            if (++consecutive_timeouts >= 5) {
                emit_error("no samples from the device for 5 s (USB or device lost?)");
                exit_code = 1;
                g_stop = true;
            }
            break;
        case uhd::rx_metadata_t::ERROR_CODE_OVERFLOW:
            ++overflows;  // Samples were lost in the device/USB path. Keep going (O4).
            consecutive_timeouts = 0;
            break;
        case uhd::rx_metadata_t::ERROR_CODE_LATE_COMMAND:
            emit_error("stream start time was already in the past (late command)");
            exit_code = 1;
            g_stop = true;
            break;
        default:
            ++other_errors;
            if (other_errors > 100) {
                emit_error("too many stream errors: " + md.strerror());
                exit_code = 1;
                g_stop = true;
            }
            break;
        }

        if (n > 0) {
            if (!started) {
                started = true;
                std::ostringstream js;
                js.precision(6);
                js << std::fixed << "{\"event\":\"started\",\"t\":" << host_now()
                   << ",\"device_time\":" << (md.has_time_spec ? md.time_spec.get_real_secs() : -1.0)
                   << ",\"time_offset_s\":" << time_offset_s << "}";
                emit(js.str());
            }
            samples += n;
            if (chunk != nullptr) {
                chunk->nsamps = n;
                chunk->has_time = md.has_time_spec;
                chunk->time_spec = md.has_time_spec ? md.time_spec.get_real_secs() : 0.0;
                chunk->overflows_at = overflows;
                chunk->host_drops_at = host_drops;
                pool.push_ready(static_cast<size_t>(idx));
                chunk = nullptr;
            } else {
                ++host_drops;
            }
        }
        if (chunk != nullptr) pool.give_back(static_cast<size_t>(idx));  // Nothing received.

        double now = host_now();
        if (now - last_status >= opt.status_interval) {
            uint64_t bytes = 0;
            for (auto& w : writers) bytes += w->total_bytes();
            double rate_mbps = (bytes - bytes_at_last_status) / (now - last_status) / 1e6;
            bytes_at_last_status = bytes;
            last_status = now;
            bool ref = true;
            try {
                if (opt.clock_source != "internal") ref = check_ref_locked(usrp);
            } catch (const std::exception&) {
                ref = false;
            }
            std::ostringstream js;
            js.precision(3);
            js << std::fixed << "{\"event\":\"status\",\"t\":" << now << ",\"streaming\":" << (started ? "true" : "false")
               << ",\"ref_locked\":" << (ref ? "true" : "false") << ",\"samples\":" << samples
               << ",\"overflows\":" << overflows << ",\"host_drops\":" << host_drops << ",\"timeouts\":" << timeouts
               << ",\"segment\":" << writers[0]->segment_index() << ",\"bytes\":" << bytes
               << ",\"rate_mbps\":" << rate_mbps << "}";
            emit(js.str());
        }
    }

    // ---- Stop -------------------------------------------------------------
    try {
        uhd::stream_cmd_t stop_cmd(uhd::stream_cmd_t::STREAM_MODE_STOP_CONTINUOUS);
        rx->issue_stream_cmd(stop_cmd);
        // Drain what is left in the device so it stops cleanly.
        for (int i = 0; i < 50; ++i) {
            std::vector<void*> ptrs(num_ch);
            for (size_t c = 0; c < num_ch; ++c) ptrs[c] = scratch[c].data();
            if (rx->recv(ptrs, chunk_samps, md, 0.1, false) == 0) break;
        }
    } catch (const std::exception&) {
    }
    pool.finish();
    writer.join();
    for (auto& w : writers) w->close(false);
    if (writer_failed) {
        emit_error("writer failed: " + writer_error);
        exit_code = 1;
    }
    std::ostringstream js;
    js << "{\"event\":\"stopped\",\"samples\":" << samples << ",\"overflows\":" << overflows
       << ",\"host_drops\":" << host_drops << ",\"exit_code\":" << exit_code << "}";
    emit(js.str());
    return exit_code;
}
